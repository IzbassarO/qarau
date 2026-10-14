"""CLI: python -m qarau_pipeline {export,run} ..."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pycolmap

from .export import export_bundle, frames_from_images, frames_from_json
from .interfaces import StageError, run_stage


def cmd_export(a: argparse.Namespace) -> None:
    out = Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    if a.images:
        frames = frames_from_images(Path(a.images), out)
    else:
        frames = frames_from_json(out)
    rec = pycolmap.Reconstruction(str(a.sparse))
    cam = next(iter(rec.cameras.values()))
    source = {
        "kind": "images",
        "name": Path(a.images).resolve().parent.name if a.images else out.name,
        "duration_s": float(len(frames) - 1),
        "fps": 1.0,
        "width": cam.width,
        "height": cam.height,
    }
    res = run_stage(
        out,
        "export",
        f"pycolmap {pycolmap.__version__}",
        {"sparse": str(a.sparse)},
        lambda: export_bundle(rec, frames, out, source, out.name),
    )
    print(res.metrics)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="qarau_pipeline")
    sub = p.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export", help="COLMAP model -> LAZ/PLY + viewer bundle")
    e.add_argument("--sparse", required=True)
    e.add_argument("--images", help="image dir (image-set input); omit to use <out>/frames.json")
    e.add_argument("--out", required=True)
    e.set_defaults(fn=cmd_export)
    a = p.parse_args(argv)
    try:
        a.fn(a)
    except StageError as err:
        print(f"ERROR: {err}", file=sys.stderr)
        sys.exit(1)
