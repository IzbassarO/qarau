"""COLMAP dense stage on a remote GPU worker behind a provider interface (ADR 0016).

The executor guarantees: a worker that was started is always stopped (finally, retried, a
failed stop is a loud error naming the worker); one wall-clock deadline for the whole job;
no worker is started when the day's GPU-hour budget cannot cover the job's estimate.
The only provider is the fake one (dense_fake.py) until the owner approves a paid account.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from functools import partial
from pathlib import Path
from typing import Any, Protocol, TypeVar

import yaml

from .interfaces import StageError

log = logging.getLogger(__name__)

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config.yaml"
STOP_ATTEMPTS = 3
# Paths on the worker; the image's workdir layout, not configuration.
REMOTE_IMAGES = "/work/images"
REMOTE_SPARSE = "/work/sparse"
REMOTE_DENSE = "/work/dense"
RESULTS = ("fused.ply", "fused.ply.vis")

Clock = Callable[[], datetime]
T = TypeVar("T")


class DenseExecutorError(StageError):
    """The remote dense job failed; the message is shown to the user."""


class DenseTimeoutError(DenseExecutorError):
    pass


class BudgetExceededError(DenseExecutorError):
    pass


class DenseConfigError(DenseExecutorError):
    pass


@dataclass(frozen=True)
class WorkerSpec:
    image: str
    gpu_type: str
    est_hours: float


@dataclass(frozen=True)
class WorkerHandle:
    id: str
    provider: str
    started_at: datetime


class GpuProvider(Protocol):
    """A GPU worker backend. timeout_s is the time left on the job's deadline; a call that
    cannot finish in it raises TimeoutError. start_worker either returns a handle or leaves
    nothing running. stop must be idempotent (stopping a stopped worker is a no-op)."""

    name: str

    def start_worker(self, spec: WorkerSpec, timeout_s: float) -> WorkerHandle: ...

    def upload(
        self, handle: WorkerHandle, local_dir: Path, remote_dir: str, timeout_s: float
    ) -> None: ...

    def run(self, handle: WorkerHandle, argv: list[str], timeout_s: float) -> int: ...

    def download(
        self, handle: WorkerHandle, remote_path: str, local_dir: Path, timeout_s: float
    ) -> None: ...

    def stop(self, handle: WorkerHandle) -> None: ...


@dataclass(frozen=True)
class DenseRemoteConfig:
    enabled: bool
    timeout_s: float
    daily_gpu_hours_budget: float
    est_hours_per_job: float
    ledger_path: Path
    image: str
    gpu_type: str


def load_dense_remote_config(path: Path = DEFAULT_CONFIG) -> DenseRemoteConfig:
    """Read the dense_remote section; any missing or invalid value names its key."""
    raw = yaml.safe_load(path.read_text()) or {}
    sec = raw.get("dense_remote") if isinstance(raw, dict) else None
    if not isinstance(sec, dict):
        raise DenseConfigError(f"{path}: section dense_remote is missing")

    def get(key: str) -> Any:
        if sec.get(key) is None:
            raise DenseConfigError(f"{path}: dense_remote.{key} is missing")
        return sec[key]

    def number(key: str, allow_zero: bool = False) -> float:
        v = get(key)
        ok = isinstance(v, int | float) and not isinstance(v, bool)
        if not ok or v < 0 or (v == 0 and not allow_zero):
            need = ">= 0" if allow_zero else "> 0"
            raise DenseConfigError(f"{path}: dense_remote.{key} must be a number {need}, got {v!r}")
        return float(v)

    def text(key: str) -> str:
        v = get(key)
        if not isinstance(v, str) or not v.strip():
            raise DenseConfigError(f"{path}: dense_remote.{key} must be a non-empty string")
        return v

    enabled = get("enabled")
    if not isinstance(enabled, bool):
        raise DenseConfigError(f"{path}: dense_remote.enabled must be true or false")
    cfg = DenseRemoteConfig(
        enabled=enabled,
        timeout_s=number("timeout_s"),
        daily_gpu_hours_budget=number("daily_gpu_hours_budget", allow_zero=True),
        est_hours_per_job=number("est_hours_per_job"),
        ledger_path=path.parent / text("ledger_path"),
        image=text("image"),
        gpu_type=text("gpu_type"),
    )
    # The estimate is what the budget check books, so it must cover the longest a job can run.
    if cfg.timeout_s > cfg.est_hours_per_job * 3600:
        raise DenseConfigError(
            f"{path}: dense_remote.timeout_s ({cfg.timeout_s:g} s) is longer than "
            f"dense_remote.est_hours_per_job ({cfg.est_hours_per_job:g} h)"
        )
    return cfg


class GpuLedger:
    """GPU hours per UTC date in a JSON file ({"2026-10-17": 1.5}). Days are only added or
    updated, never removed."""

    # ponytail: no file lock; two processes settling at the same instant can lose an update.
    # Add fcntl.flock when more than one dense job runs at a time.

    def __init__(self, path: Path) -> None:
        self.path = path

    def _read(self) -> dict[str, float]:
        return json.loads(self.path.read_text()) if self.path.exists() else {}

    def used(self, day: date) -> float:
        return float(self._read().get(day.isoformat(), 0.0))

    def add(self, day: date, hours: float) -> None:
        data = self._read()
        data[day.isoformat()] = round(data.get(day.isoformat(), 0.0) + hours, 6)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
        os.replace(tmp, self.path)


def dense_commands(images: str, sparse: str, dense: str) -> list[list[str]]:
    """COLMAP dense argv: undistort → PatchMatch (geometric consistency) → fusion."""
    return [
        ["colmap", "image_undistorter", "--image_path", images, "--input_path", sparse,
         "--output_path", dense, "--output_type", "COLMAP"],
        ["colmap", "patch_match_stereo", "--workspace_path", dense,
         "--workspace_format", "COLMAP", "--PatchMatchStereo.geom_consistency", "true"],
        ["colmap", "stereo_fusion", "--workspace_path", dense, "--workspace_format", "COLMAP",
         "--input_type", "geometric", "--output_path", f"{dense}/fused.ply"],
    ]  # fmt: skip


def _utc_now() -> datetime:
    return datetime.now(UTC)


class RemoteDenseExecutor:
    def __init__(
        self,
        provider: GpuProvider,
        config: DenseRemoteConfig,
        ledger: GpuLedger,
        clock: Clock = _utc_now,
    ) -> None:
        self.provider, self.config, self.ledger, self.clock = provider, config, ledger, clock

    def run(self, frames_dir: Path, sparse_dir: Path, out_dir: Path) -> Path:
        """Dense-reconstruct on a remote worker; return out_dir/fused.ply (+ .vis next to it)."""
        cfg = self.config
        if not cfg.enabled:
            raise DenseExecutorError("dense_remote.enabled is false in config.yaml")
        spec = WorkerSpec(cfg.image, cfg.gpu_type, cfg.est_hours_per_job)
        t0 = self.clock()
        day = t0.astimezone(UTC).date()
        used = self.ledger.used(day)
        if used + spec.est_hours > cfg.daily_gpu_hours_budget:
            raise BudgetExceededError(
                f"GPU budget: {used:g} h used on {day} + {spec.est_hours:g} h for this job > "
                f"daily_gpu_hours_budget {cfg.daily_gpu_hours_budget:g} h; no worker started"
            )
        # Book the estimate up front so a crash mid-job never under-counts; settled below.
        self.ledger.add(day, spec.est_hours)
        deadline = t0 + timedelta(seconds=cfg.timeout_s)
        try:
            handle = self._call(
                deadline, "start_worker", lambda t: self.provider.start_worker(spec, t), False
            )
            try:
                self._remaining(deadline, "start_worker")  # returned past the deadline
                self._job(handle, deadline, frames_dir, sparse_dir, out_dir)
            finally:
                self._stop(handle)
        finally:
            hours = (self.clock() - t0).total_seconds() / 3600
            self.ledger.add(day, hours - spec.est_hours)
        return out_dir / RESULTS[0]

    def _job(
        self, h: WorkerHandle, deadline: datetime, frames: Path, sparse: Path, out: Path
    ) -> None:
        p = self.provider
        self._call(deadline, "upload", lambda t: p.upload(h, frames, REMOTE_IMAGES, t))
        self._call(deadline, "upload", lambda t: p.upload(h, sparse, REMOTE_SPARSE, t))
        for argv in dense_commands(REMOTE_IMAGES, REMOTE_SPARSE, REMOTE_DENSE):
            code = self._call(deadline, argv[1], partial(p.run, h, argv))
            if code != 0:
                raise DenseExecutorError(f"{argv[1]} exited with {code} on worker {h.id}")
        out.mkdir(parents=True, exist_ok=True)
        for name in RESULTS:
            self._call(deadline, "download", partial(p.download, h, f"{REMOTE_DENSE}/{name}", out))
            if not (out / name).is_file():
                raise DenseExecutorError(f"download: {name} missing from worker {h.id}")

    def _remaining(self, deadline: datetime, step: str) -> float:
        left = (deadline - self.clock()).total_seconds()
        if left <= 0:
            raise DenseTimeoutError(f"{step}: dense job passed timeout_s {self.config.timeout_s:g}")
        return left

    def _call(
        self, deadline: datetime, step: str, fn: Callable[[float], T], check_after: bool = True
    ) -> T:
        left = self._remaining(deadline, step)
        try:
            result = fn(left)
        except TimeoutError as err:
            raise DenseTimeoutError(f"{step}: provider timed out ({err})") from err
        if check_after:
            self._remaining(deadline, step)
        return result

    def _stop(self, handle: WorkerHandle) -> None:
        # ponytail: retries back to back; add backoff when a real provider's API needs it.
        last: Exception | None = None
        for attempt in range(1, STOP_ATTEMPTS + 1):
            try:
                self.provider.stop(handle)
                return
            except Exception as err:  # noqa: BLE001 - retried, then raised below
                last = err
                log.warning("stop worker %s: attempt %d/%d failed: %s",
                            handle.id, attempt, STOP_ATTEMPTS, err)  # fmt: skip
        log.error("worker %s (%s) may still be running", handle.id, handle.provider)
        raise DenseExecutorError(
            f"could not stop worker {handle.id} ({handle.provider}) after {STOP_ATTEMPTS} "
            f"attempts, it may still be running and billing: {last}"
        ) from last
