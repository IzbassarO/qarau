"""CLI: python -m qarau_pipeline {run,export,segment,project,benchmark} ..."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from . import frames as frames_stage
from .interfaces import StageError, run_stage
from .segmentation import GroundedSam2, ManualMasks, SamApiSegmenter, parse_prompt, segment_stage


def _ffmpeg_version() -> str:
    try:
        out = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True).stdout
        return out.split()[2]
    except (OSError, IndexError):
        return "unknown"


# pycolmap is imported inside the commands that need it: it bundles its own libomp, which makes
# torch abort in the same process (segment stage), so the segment path must not load it.


def _export(out: Path, sparse: Path, frames: list[dict[str, Any]], source: dict[str, Any]) -> None:
    import pycolmap

    from .export import export_bundle

    def go() -> dict[str, Any]:
        rec = pycolmap.Reconstruction(str(sparse))
        return export_bundle(rec, frames, out, source, out.name)

    res = run_stage(out, "export", f"pycolmap {pycolmap.__version__}", {"sparse": str(sparse)}, go)
    print("export:", res.metrics)


def cmd_export(a: argparse.Namespace) -> None:
    import pycolmap

    from .export import frames_from_images, frames_from_json

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
    _export(out, Path(a.sparse), frames, source)


def cmd_run(a: argparse.Namespace) -> None:
    import pycolmap

    from .export import frames_from_json
    from .reconstruction import ColmapReconstructor

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
    dropped = [Path(f["file"]).name for f in fj["frames"] if not f["kept"]]
    rc = ColmapReconstructor(
        mode="video" if a.video else "images",
        max_image_size=a.max_image_size,
        max_num_features=a.max_features,
        peak_threshold=a.peak_threshold,
        mapper=a.mapper,
        merge=a.merge,
    )
    res = run_stage(
        out, "reconstruction", f"pycolmap {pycolmap.__version__}", rc.params(),
        lambda: rc.reconstruct(out / "frames", names, out / "colmap", dropped),
    )  # fmt: skip
    print("reconstruction:", res.metrics)
    _export(out, out / "colmap/sparse/0", frames_from_json(out), fj["source"])


def cmd_segment(a: argparse.Namespace) -> None:
    run = Path(a.run).resolve()
    if a.segmenter == "manual":
        if not a.masks:
            raise StageError("--segmenter manual needs --masks DIR")
        seg: Any = ManualMasks(a.masks)
    else:
        seg = (GroundedSam2(box_threshold=a.box_threshold) if a.segmenter == "grounded-sam2"
               else SamApiSegmenter())  # fmt: skip
    prompts = [parse_prompt(p) for p in a.prompt]
    print("segment:", segment_stage(run, seg, prompts, a.frames, a.stride))


def cmd_project(a: argparse.Namespace) -> None:
    from .projection import project_stage

    print("project:", project_stage(Path(a.run).resolve(), a.scale))


def cmd_benchmark(a: argparse.Namespace) -> None:
    from .benchmark import run_benchmark

    only = [n for s in a.only for n in s.split(",") if n] if a.only else None
    sys.exit(run_benchmark(a.data, a.out, only, a.timeout_s, a.record))


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
    r.add_argument("--mapper", choices=["incremental", "global"], default="incremental")
    r.add_argument("--no-merge", dest="merge", action="store_false",
                   help="largest model only, no leftover registration (pre-P3)")  # fmt: skip
    r.set_defaults(fn=cmd_run)
    e = sub.add_parser("export", help="COLMAP model -> LAZ/PLY + viewer bundle")
    e.add_argument("--sparse", required=True)
    e.add_argument("--images", help="image dir (image-set input); omit to use <out>/frames.json")
    e.add_argument("--out", required=True)
    e.set_defaults(fn=cmd_export)
    sg = sub.add_parser("segment", help="masks for the bundle's frames -> <run>/segmentation/")
    sg.add_argument("--run", required=True)
    sg.add_argument("--segmenter", choices=["manual", "grounded-sam2", "sam-api"], required=True)
    sg.add_argument("--masks", help="manual: directory with masks")
    sg.add_argument("--prompt", action="append", required=True, metavar="TEXT[:CLASS[:SEVERITY]]")
    sg.add_argument("--frames", choices=["registered", "all", "stride"], default="registered")
    sg.add_argument("--stride", type=int, default=5)
    sg.add_argument("--box-threshold", type=float, default=0.3, help="grounded-sam2 only")
    sg.set_defaults(fn=cmd_segment)
    pj = sub.add_parser("project", help="masks -> labelled 3D points -> <run>/findings/")
    pj.add_argument("--run", required=True)
    pj.add_argument("--scale", type=float, help="metres per world unit (area in m2 only with it)")
    pj.set_defaults(fn=cmd_project)
    b = sub.add_parser("benchmark", help="all reference videos -> <out>/results.json")
    b.add_argument("--data", required=True, help="dir with the reference videos (read-only)")
    b.add_argument("--out", required=True)
    b.add_argument("--only", nargs="+", metavar="NAME", help="manifest names to run (a,b or a b)")
    b.add_argument("--timeout-s", type=float, default=1800, help="per video (default 1800)")
    b.add_argument("--record", action="store_true", help="also write docs/benchmarks/<sha>.json"
                   " and append rows to docs/notes/benchmark.md")  # fmt: skip
    b.set_defaults(fn=cmd_benchmark)
    a = p.parse_args(argv)
    try:
        a.fn(a)
    except StageError as err:
        print(f"ERROR: {err}", file=sys.stderr)
        sys.exit(1)
