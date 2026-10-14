"""CLI: python -m qarau_pipeline {run,export,dense} ..."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pycolmap

from . import frames as frames_stage
from .export import export_bundle, frames_from_images, frames_from_json
from .interfaces import StageError, run_stage
from .reconstruction import ColmapReconstructor


def _ffmpeg_version() -> str:
    try:
        out = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True).stdout
        return out.split()[2]
    except (OSError, IndexError):
        return "unknown"


def _export(
    out: Path,
    sparse: Path,
    frames: list[dict[str, Any]],
    source: dict[str, Any],
    dense: bool = False,
) -> None:
    def go() -> dict[str, Any]:
        rec = pycolmap.Reconstruction(str(sparse))
        return export_bundle(rec, frames, out, source, out.name, out / "dense" if dense else None)

    res = run_stage(
        out, "export", f"pycolmap {pycolmap.__version__}",
        {"sparse": str(sparse), "dense": dense}, go,
    )  # fmt: skip
    print("export:", res.metrics)


def cmd_export(a: argparse.Namespace) -> None:
    out = Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    if a.images:
        frames = frames_from_images(Path(a.images), out)
        cam = next(iter(pycolmap.Reconstruction(str(a.sparse)).cameras.values()))
        source = {
            "kind": "images",
            "name": Path(a.images).resolve().parent.name,
            "duration_s": float(len(frames) - 1),
            "fps": 1.0,
            "width": cam.width,
            "height": cam.height,
        }
    else:
        frames = frames_from_json(out)
        source = json.loads((out / "frames.json").read_text())["source"]
    _export(out, Path(a.sparse), frames, source, a.dense)


def cmd_dense(a: argparse.Namespace) -> None:
    from .dense import DepthAnythingDensifier  # needs the ml group

    run = Path(a.run).resolve()
    d = DepthAnythingDensifier(
        frame_stride=a.frame_stride, pixel_stride=a.pixel_stride, voxel_frac=a.voxel_frac
    )
    res = run_stage(
        run, "dense", d.model, d.params(), lambda: d.densify(run, run / "dense")
    )  # fmt: skip
    print("dense:", {k: v for k, v in res.metrics.items() if k != "consistency"})


def cmd_run(a: argparse.Namespace) -> None:
    out = Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    if a.video:
        video = Path(a.video).resolve()
        params = {"video": video.name, "max_frames": a.max_frames}
        run_stage(
            out, "frames", f"ffmpeg {_ffmpeg_version()}", params,
            lambda: frames_stage.run(video, out, a.max_frames),
        )  # fmt: skip
    else:
        run_stage(
            out, "frames", "link", {"images": str(a.images)},
            lambda: frames_stage.from_images(Path(a.images).resolve(), out),
        )  # fmt: skip
    fj = json.loads((out / "frames.json").read_text())
    names = [Path(f["file"]).name for f in fj["frames"] if f["kept"]]
    rc = ColmapReconstructor(
        mode="video" if a.video else "images",
        max_image_size=a.max_image_size,
        max_num_features=a.max_features,
        peak_threshold=a.peak_threshold,
    )
    res = run_stage(
        out, "reconstruction", f"pycolmap {pycolmap.__version__}", rc.params(),
        lambda: rc.reconstruct(out / "frames", names, out / "colmap"),
    )  # fmt: skip
    print("reconstruction:", res.metrics)
    _export(out, out / "colmap/sparse/0", frames_from_json(out), fj["source"])


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="qarau_pipeline")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="video or image set -> frames -> COLMAP -> viewer bundle")
    g = r.add_mutually_exclusive_group(required=True)
    g.add_argument("--video")
    g.add_argument("--images")
    r.add_argument("--out", required=True)
    r.add_argument("--max-frames", type=int, help="upper cap on extracted frames (default 300)")
    r.add_argument("--max-image-size", type=int, default=1600)
    r.add_argument("--max-features", type=int, help="SIFT features per image (default 8192)")
    r.add_argument("--peak-threshold", type=float, help="SIFT peak threshold (default 0.00667)")
    r.set_defaults(fn=cmd_run)
    e = sub.add_parser("export", help="COLMAP model -> LAZ/PLY + viewer bundle")
    e.add_argument("--sparse", required=True)
    e.add_argument("--images", help="image dir (image-set input); omit to use <out>/frames.json")
    e.add_argument("--out", required=True)
    e.add_argument("--dense", action="store_true", help="merge <out>/dense/ into the bundle (v2)")
    e.set_defaults(fn=cmd_export)
    d = sub.add_parser("dense", help="densify a reconstructed run (needs: uv run --group ml)")
    d.add_argument("--run", required=True)
    d.add_argument("--frame-stride", type=int, default=1)
    d.add_argument("--pixel-stride", type=int, default=3)
    d.add_argument("--voxel-frac", type=float, default=0.0013)
    d.set_defaults(fn=cmd_dense)
    a = p.parse_args(argv)
    try:
        a.fn(a)
    except StageError as err:
        print(f"ERROR: {err}", file=sys.stderr)
        sys.exit(1)
