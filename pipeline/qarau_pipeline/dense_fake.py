"""Fake GpuProvider for tests and the `dense-remote --provider fake` demo (ADR 0016).

A "worker" is a local directory; remote paths map under it. Every call is recorded. Any
step can raise, hang (advance the injected clock) or, for run, exit non-zero. No network,
no subprocess, nothing costs money. Its stereo_fusion writes synthetic points from the
uploaded sparse model (fake_fusion), marked as fake in the PLY header (ADR 0017).
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np

from .dense_remote import REMOTE_SPARSE, WorkerHandle, WorkerSpec
from .visibility import FAKE_COMMENT, write_ply, write_vis


def fake_fusion(sparse_dir: Path, ply: Path, per_point: int = 4, seed: int = 0) -> None:
    """Stand-in for stereo_fusion: per sparse point, per_point copies jittered by a quarter of
    the median sparse spacing; normal = towards the mean camera of its track; .vis row = its
    track as dense workspace indices (registered images in ascending image id, as COLMAP)."""
    import pycolmap
    from scipy.spatial import cKDTree

    rec = pycolmap.Reconstruction(str(sparse_dir))
    reg = sorted(rec.reg_image_ids())
    idx = {iid: k for k, iid in enumerate(reg)}
    centers = np.array([rec.images[i].projection_center() for i in reg]).reshape(-1, 3)
    pids = sorted(rec.points3D)
    xyz = np.array([rec.points3D[p].xyz for p in pids]).reshape(-1, 3)
    rgb = np.array([rec.points3D[p].color for p in pids], np.uint8).reshape(-1, 3)
    rows = [sorted({idx[e.image_id] for e in rec.points3D[p].track.elements if e.image_id in idx})
            for p in pids]  # fmt: skip
    normals = np.array([centers[r].mean(0) if r else np.zeros(3) for r in rows]).reshape(-1, 3)
    normals -= xyz
    normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-12)
    spacing = float(np.median(cKDTree(xyz).query(xyz, k=2)[0][:, 1])) if len(xyz) > 1 else 0.0
    rng = np.random.default_rng(seed)
    dense = np.repeat(xyz, per_point, 0) + rng.normal(0, spacing / 4, (len(xyz) * per_point, 3))
    write_ply(ply, dense, np.repeat(rgb, per_point, 0), np.repeat(normals, per_point, 0),
              FAKE_COMMENT)  # fmt: skip
    counts = np.repeat([len(r) for r in rows], per_point)
    ids = np.array([i for r in rows for _ in range(per_point) for i in r], np.uint32)
    write_vis(ply.with_name(ply.name + ".vis"), np.concatenate([[0], np.cumsum(counts)]), ids)


class FakeClock:
    def __init__(self, now: datetime | None = None) -> None:
        self.now = now or datetime(2026, 10, 17, 9, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


@dataclass
class FakeGpuProvider:
    root: Path
    clock: FakeClock = field(default_factory=FakeClock)
    # step name (start_worker / upload / run / download) -> exception raised by that call
    fail: dict[str, BaseException] = field(default_factory=dict)
    # step name -> seconds the call takes; past the given timeout_s it raises TimeoutError,
    # or with respect_timeout=False returns late anyway (a misbehaving provider)
    hang: dict[str, float] = field(default_factory=dict)
    respect_timeout: bool = True
    exit_code: int = 0
    stop_failures: int = 0  # the first N stop calls raise
    step_s: float = 1.0  # clock time every call takes
    name: str = "fake"
    calls: list[tuple[str, ...]] = field(default_factory=list)
    running: set[str] = field(default_factory=set)

    def _step(self, step: str, timeout_s: float, *args: str) -> None:
        self.calls.append((step, *args))
        took = self.hang.get(step, self.step_s)
        if took > timeout_s and self.respect_timeout:
            self.clock.advance(timeout_s)
            raise TimeoutError(f"fake {step} took longer than {timeout_s:g} s")
        self.clock.advance(took)
        if step in self.fail:
            raise self.fail[step]

    def _remote(self, handle: WorkerHandle, path: str) -> Path:
        return self.root / handle.id / path.lstrip("/")

    def start_worker(self, spec: WorkerSpec, timeout_s: float) -> WorkerHandle:
        self._step("start_worker", timeout_s, spec.image)
        handle = WorkerHandle(f"fake-{len(self.calls)}", self.name, self.clock())
        self.running.add(handle.id)
        return handle

    def upload(
        self, handle: WorkerHandle, local_dir: Path, remote_dir: str, timeout_s: float
    ) -> None:
        self._step("upload", timeout_s, str(local_dir), remote_dir)
        shutil.copytree(local_dir, self._remote(handle, remote_dir), dirs_exist_ok=True)

    def run(self, handle: WorkerHandle, argv: list[str], timeout_s: float) -> int:
        self._step("run", timeout_s, *argv)
        if self.exit_code == 0 and argv[1] == "stereo_fusion":
            ply = self._remote(handle, argv[argv.index("--output_path") + 1])
            ply.parent.mkdir(parents=True, exist_ok=True)
            sparse = self._remote(handle, REMOTE_SPARSE)
            if (sparse / "points3D.bin").exists() or (sparse / "points3D.txt").exists():
                fake_fusion(sparse, ply)
                return self.exit_code
            ply.write_text("ply\nformat ascii 1.0\nelement vertex 0\nend_header\n")
            ply.with_name(ply.name + ".vis").write_bytes(b"\0" * 8)
        return self.exit_code

    def download(
        self, handle: WorkerHandle, remote_path: str, local_dir: Path, timeout_s: float
    ) -> None:
        self._step("download", timeout_s, remote_path, str(local_dir))
        src = self._remote(handle, remote_path)
        if src.exists():  # a missing file is the executor's problem to notice
            shutil.copy2(src, local_dir / src.name)

    def stop(self, handle: WorkerHandle) -> None:
        self.calls.append(("stop", handle.id))
        if self.stop_failures > 0:
            self.stop_failures -= 1
            raise RuntimeError("fake stop failure")
        self.running.discard(handle.id)
