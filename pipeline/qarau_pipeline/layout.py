"""Run directory layout (ADR 0022): every workflow step writes under `<run>/<step>/`.

    <run>/probe/probe.json
    <run>/frames/000000.jpg …, manifest.json, frames.json, quality_report.json
    <run>/sparse/database.db, masks/, sparse/0
    <run>/export/web/manifest.json + arrays, cloud.laz, cloud.ply
    <run>/dense/fused.ply (+ .vis)
    <run>/run.json, log.txt, workflow.json, video.<ext>      (run-wide files stay at the root)

Runs made before S1 keep `frames.json`, `quality_report.json`, `colmap/` and `web/` at the run
root. Every helper returns the new path unless the run dir already has the legacy one (and not
the new one), so old runs are read and re-exported in place, new runs get the new layout.
"""

from __future__ import annotations

from pathlib import Path


def _pick(new: Path, legacy: Path) -> Path:
    return legacy if legacy.exists() and not new.exists() else new


def probe_dir(run: Path) -> Path:
    return run / "probe"


def frames_dir(run: Path) -> Path:
    return run / "frames"


def frames_json(run: Path) -> Path:
    return _pick(run / "frames" / "frames.json", run / "frames.json")


def quality_report(run: Path) -> Path:
    return _pick(run / "frames" / "quality_report.json", run / "quality_report.json")


def sparse_dir(run: Path) -> Path:
    """The COLMAP work dir: database.db, masks/, sparse/0 (the model: `sparse_model`)."""
    return _pick(run / "sparse", run / "colmap")


def sparse_model(run: Path) -> Path:
    return sparse_dir(run) / "sparse" / "0"


def export_dir(run: Path) -> Path:
    """Holds web/, cloud.laz and cloud.ply: `<run>/export`, or the run root for a legacy run."""
    legacy = (run / "web" / "manifest.json").exists() and not (run / "export").exists()
    return run if legacy else run / "export"


def manifest_path(run: Path) -> Path:
    return export_dir(run) / "web" / "manifest.json"


def dense_dir(run: Path) -> Path:
    return run / "dense"
