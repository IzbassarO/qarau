"""Shared types: StageResult, run.json bookkeeping, the Reconstructor protocol."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import numpy as np


@dataclass
class StageResult:
    name: str
    status: str  # "ok" | "failed"
    started_at: str
    finished_at: str
    duration_s: float
    tool_version: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


class StageError(RuntimeError):
    """A stage failed honestly; the message is shown to the user."""


class Reconstructor(Protocol):
    def reconstruct(
        self, frames_dir: Path, names: list[str], out_dir: Path, bridges: list[str] | None = None
    ) -> dict[str, Any]:
        """Reconstruct the named images into out_dir (database.db, sparse/0); return metrics.
        bridges: dropped frames that may only link parts of the map, never end up in it."""
        ...


@dataclass
class MaskResult:
    prompt: str
    mask: np.ndarray  # H x W bool, frame resolution
    confidence: float  # 0..1
    bbox: tuple[int, int, int, int]  # x0, y0, x1, y1


class Segmenter(Protocol):
    """Frame + text prompts -> instance masks. Contract: docs/notes/m2-plan.md section 3."""

    name: str

    def info(self) -> dict[str, Any]:
        """name, version, licence, dev_only"""
        ...

    def segment(self, image: np.ndarray, prompts: list[str], frame: int) -> list[MaskResult]:
        """image: H x W x 3 RGB uint8; frame: index into frames.json (for file-based masks)."""
        ...


class Densifier(Protocol):
    def densify(self, run_dir: Path, out_dir: Path) -> dict[str, Any]:
        """Dense points for a reconstructed run into out_dir; return metrics."""
        ...


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def read_run(out_dir: Path) -> list[dict[str, Any]]:
    p = out_dir / "run.json"
    return json.loads(p.read_text()) if p.exists() else []


def record(out_dir: Path, res: StageResult) -> None:
    """Append to run.json; a re-run of a stage replaces its previous entry (idempotent)."""
    stages = [s for s in read_run(out_dir) if s["name"] != res.name]
    stages.append(asdict(res))
    (out_dir / "run.json").write_text(json.dumps(stages, indent=2))


def run_stage(
    out_dir: Path,
    name: str,
    tool_version: str,
    params: dict[str, Any],
    fn: Callable[[], dict[str, Any]],
) -> StageResult:
    """Time fn(), record the result in run.json; on failure record it and re-raise."""
    out_dir.mkdir(parents=True, exist_ok=True)
    started, t0 = _now(), time.perf_counter()
    status, metrics, error = "ok", {}, None
    try:
        metrics = fn()
    except Exception as e:  # noqa: BLE001 - recorded, then re-raised
        status, error = "failed", f"{type(e).__name__}: {e}"
    res = StageResult(
        name, status, started, _now(), round(time.perf_counter() - t0, 2),
        tool_version, params, metrics, error,
    )  # fmt: skip
    record(out_dir, res)
    if error:
        raise StageError(f"stage '{name}' failed: {error}")
    return res


def stage_seconds(out_dir: Path) -> dict[str, float]:
    return {s["name"]: s["duration_s"] for s in read_run(out_dir) if s["status"] == "ok"}
