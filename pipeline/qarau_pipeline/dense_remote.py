"""COLMAP dense stage on a remote GPU worker behind a provider interface (ADR 0016).

The executor guarantees: a worker that was started is always stopped (finally, retried, a
failed stop is a loud error naming the worker); one wall-clock deadline for the whole job;
no worker is started when the day's GPU-hour budget cannot cover the job's estimate.
Providers: the fake one (dense_fake.py) and Modal (dense_modal.py, ADR 0019), which is not
called until the owner approves a paid account (P4a-2a-run).
"""

from __future__ import annotations

import json
import logging
import os
import re
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
PROVIDERS = ("fake", "modal")  # dense_remote.provider; absent means fake
# CUDA arch each GPU type needs in dense_remote.cuda_architectures (Modal GPU names).
GPU_ARCH = {"T4": "75", "A100": "80", "A10": "86", "L4": "89", "L40S": "89", "H100": "90"}

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
class PatchMatchKnobs:
    """dense_remote.patch_match: COLMAP PatchMatch speed knobs; None keeps COLMAP's default
    (docs/notes/dense-speed.md). Ranges are COLMAP 4.2.1's PatchMatchOptions::Check."""

    max_image_size: int | None = None  # image_undistorter and PatchMatchStereo; default: full
    num_iterations: int | None = None  # default 5
    window_radius: int | None = None  # default 5, at most 32
    window_step: int | None = None  # default 1, 1 or 2
    num_sources: int | None = None  # source frames per view; default 20 (patch-match.cfg)


NO_KNOBS = PatchMatchKnobs()

# key -> (lowest, highest) allowed; highest None = no upper bound
PATCH_MATCH_RANGES: dict[str, tuple[int, int | None]] = {
    "max_image_size": (1, None),
    "num_iterations": (1, None),
    "window_radius": (1, 32),
    "window_step": (1, 2),
    "num_sources": (1, None),
}


@dataclass(frozen=True)
class DenseRemoteConfig:
    enabled: bool
    timeout_s: float
    daily_gpu_hours_budget: float
    est_hours_per_job: float
    ledger_path: Path
    image: str
    gpu_type: str
    provider: str = "fake"
    cuda_architectures: str = "89"  # the image's CUDA_ARCHITECTURES build arg, e.g. "75;86;90"
    gpu_fallback: tuple[str, ...] = ()  # tried after gpu_type when the provider has no capacity
    start_timeout_s: float = 300.0  # per GPU type: how long to wait for capacity
    patch_match: PatchMatchKnobs = NO_KNOBS


def _patch_match(path: Path, raw: Any) -> PatchMatchKnobs:
    if raw is None:
        return NO_KNOBS
    if not isinstance(raw, dict):
        raise DenseConfigError(f"{path}: dense_remote.patch_match must be a mapping of knobs")
    if unknown := sorted(map(str, set(raw) - set(PATCH_MATCH_RANGES))):
        raise DenseConfigError(
            f"{path}: dense_remote.patch_match.{unknown[0]} is not a knob; "
            f"known: {', '.join(PATCH_MATCH_RANGES)}"
        )
    for key, v in raw.items():
        lo, hi = PATCH_MATCH_RANGES[key]
        if v is None:
            continue
        if not isinstance(v, int) or isinstance(v, bool) or v < lo or (hi is not None and v > hi):
            need = f"an integer {lo}..{hi}" if hi is not None else f"an integer >= {lo}"
            raise DenseConfigError(
                f"{path}: dense_remote.patch_match.{key} must be {need}, got {v!r}"
            )
    return PatchMatchKnobs(**raw)


def make_provider(cfg: DenseRemoteConfig) -> GpuProvider:
    """The real provider named by dense_remote.provider (the fake is built by tests and the
    demo CLI, with its own directory and clock)."""
    if cfg.provider == "modal":
        # no vendor code until a real job asks for it
        from .dense_modal import ModalGpuProvider, ModalImageSpec

        return ModalGpuProvider.from_env(
            ModalImageSpec(cuda_architectures=cfg.cuda_architectures),
            fallback_gpus=cfg.gpu_fallback,
            start_timeout_s=cfg.start_timeout_s,
        )
    raise DenseConfigError(f"dense_remote.provider {cfg.provider!r} is not a real GPU provider")


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
    provider = sec.get("provider", "fake")
    if provider not in PROVIDERS:
        raise DenseConfigError(f"{path}: dense_remote.provider must be one of {PROVIDERS}")
    cfg = DenseRemoteConfig(
        enabled=enabled,
        timeout_s=number("timeout_s"),
        daily_gpu_hours_budget=number("daily_gpu_hours_budget", allow_zero=True),
        est_hours_per_job=number("est_hours_per_job"),
        ledger_path=path.parent / text("ledger_path"),
        image=text("image"),
        gpu_type=text("gpu_type"),
        provider=provider,
        cuda_architectures=str(sec.get("cuda_architectures", "89")),
        gpu_fallback=tuple(sec.get("gpu_fallback") or ()),
        start_timeout_s=number("start_timeout_s") if "start_timeout_s" in sec else 300.0,
        patch_match=_patch_match(path, sec.get("patch_match")),
    )
    if cfg.start_timeout_s > cfg.timeout_s:
        raise DenseConfigError(
            f"{path}: dense_remote.start_timeout_s ({cfg.start_timeout_s:g} s) is longer than "
            f"dense_remote.timeout_s ({cfg.timeout_s:g} s)"
        )
    if not re.fullmatch(r"\d{2,3}(;\d{2,3})*", cfg.cuda_architectures):
        raise DenseConfigError(
            f'{path}: dense_remote.cuda_architectures must be like "89" or "75;86;90", '
            f"got {cfg.cuda_architectures!r}"
        )
    # A GPU whose arch is not in the image fails only in patch_match_stereo, after paying.
    for key, gpu in [("gpu_type", cfg.gpu_type), *(("gpu_fallback", g) for g in cfg.gpu_fallback)]:
        if (arch := GPU_ARCH.get(gpu)) and arch not in cfg.cuda_architectures.split(";"):
            raise DenseConfigError(
                f"{path}: dense_remote.{key} {gpu} needs CUDA arch {arch} in "
                f"dense_remote.cuda_architectures ({cfg.cuda_architectures!r})"
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


def dense_commands(
    images: str, sparse: str, dense: str, knobs: PatchMatchKnobs = NO_KNOBS
) -> list[list[str]]:
    """COLMAP dense argv: undistort → PatchMatch (geometric consistency) → fusion. Only the
    knobs that are set add flags; with none the argv is the pre-P4a-3 one."""
    undistort = ["colmap", "image_undistorter", "--image_path", images, "--input_path", sparse,
                 "--output_path", dense, "--output_type", "COLMAP"]  # fmt: skip
    patch_match = ["colmap", "patch_match_stereo", "--workspace_path", dense, "--workspace_format",
                   "COLMAP", "--PatchMatchStereo.geom_consistency", "true"]  # fmt: skip
    if knobs.max_image_size is not None:
        undistort += ["--max_image_size", str(knobs.max_image_size)]
    for key in ("max_image_size", "num_iterations", "window_radius", "window_step"):
        if (v := getattr(knobs, key)) is not None:
            patch_match += [f"--PatchMatchStereo.{key}", str(v)]
    commands = [undistort, patch_match]
    if knobs.num_sources is not None:
        # The source count is not a patch_match_stereo option: image_undistorter writes it to
        # stereo/patch-match.cfg as two lines per image, "<name>" then "__auto__, 20".
        # Verified 2026-10-21 with pycolmap 4.2.1 (same undistorter code as the image):
        # num_patch_match_src_images -1 writes "__auto__, 20", 8 writes "__auto__, 8". The
        # grep fails the step if no line was rewritten (a changed format is never silent).
        cfg, n = f"{dense}/stereo/patch-match.cfg", knobs.num_sources
        commands.insert(1, ["sh", "-c", f"sed -i -E 's/^__auto__, [0-9]+$/__auto__, {n}/' {cfg}"
                            f" && grep -qx '__auto__, {n}' {cfg}"])  # fmt: skip
    commands.append(
        ["colmap", "stereo_fusion", "--workspace_path", dense, "--workspace_format", "COLMAP",
         "--input_type", "geometric", "--output_path", f"{dense}/fused.ply"]
    )  # fmt: skip
    return commands


def _step(argv: list[str]) -> str:
    """The name errors give a command: the COLMAP subcommand, else the program."""
    return argv[1] if argv[0] == "colmap" else argv[0]


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
        started: datetime | None = None
        try:
            handle = self._call(
                deadline, "start_worker", lambda t: self.provider.start_worker(spec, t), False
            )
            started = self.clock()  # time queued for a GPU is not GPU time (2026-10-21: 28 min)
            try:
                self._remaining(deadline, "start_worker")  # returned past the deadline
                self._job(handle, deadline, frames_dir, sparse_dir, out_dir)
            finally:
                self._stop(handle)
        finally:
            hours = (self.clock() - started).total_seconds() / 3600 if started else 0.0
            self.ledger.add(day, hours - spec.est_hours)
        return out_dir / RESULTS[0]

    def _job(
        self, h: WorkerHandle, deadline: datetime, frames: Path, sparse: Path, out: Path
    ) -> None:
        p = self.provider
        self._call(deadline, "upload", lambda t: p.upload(h, frames, REMOTE_IMAGES, t))
        self._call(deadline, "upload", lambda t: p.upload(h, sparse, REMOTE_SPARSE, t))
        knobs = self.config.patch_match
        for argv in dense_commands(REMOTE_IMAGES, REMOTE_SPARSE, REMOTE_DENSE, knobs):
            code = self._call(deadline, _step(argv), partial(p.run, h, argv))
            if code != 0:
                raise DenseExecutorError(f"{_step(argv)} exited with {code} on worker {h.id}")
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
