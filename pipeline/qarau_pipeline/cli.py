"""CLI: python -m qarau_pipeline {run,quality,export,scale,segment,project,dense-remote,...}"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from . import frames as frames_stage
from . import quality
from .interfaces import StageError, read_run, run_stage
from .keyframes import MODES, KeyframeParams, subsample
from .segmentation import GroundedSam2, ManualMasks, SamApiSegmenter, parse_prompt, segment_stage


def _ffmpeg_version() -> str:
    try:
        out = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True).stdout
        return out.split()[2]
    except (OSError, IndexError):
        return "unknown"


# pycolmap is imported inside the commands that need it: it bundles its own libomp, which makes
# torch abort in the same process (segment stage), so the segment path must not load it.


SCALE_REF = "scale_ref.json"  # the run's scale reference (ADR 0018), copied in by run/scale


def _export(
    out: Path,
    sparse: Path,
    frames: list[dict[str, Any]],
    source: dict[str, Any],
    dense: Path | None = None,
    ref: Path | None = None,
) -> None:
    """ref: a new scale reference, stored as <out>/scale_ref.json once the export succeeded;
    None = the stored one, if any. Always from the untouched sparse model: never compounds."""
    import pycolmap

    from .export import export_bundle
    from .scale import load_reference

    stored = out / SCALE_REF
    ref_path = ref or (stored if stored.exists() else None)

    def go() -> dict[str, Any]:
        sref = load_reference(ref_path) if ref_path else None
        rec = pycolmap.Reconstruction(str(sparse))
        dp = None
        if dense is not None:
            from .visibility import load_dense, max_frames_per_point

            dp = load_dense(dense, rec, frames, max_frames_per_point())
        m = export_bundle(rec, frames, out, source, out.name, dp, sref)
        if ref is not None and ref.resolve() != stored.resolve():
            shutil.copyfile(ref, stored)
        return m

    params = {"sparse": str(sparse), **({"dense": str(dense)} if dense else {}),
              **({"scale_ref": str(ref_path)} if ref_path else {})}  # fmt: skip
    res = run_stage(out, "export", f"pycolmap {pycolmap.__version__}", params, go)
    print("export:", res.metrics)


def cmd_scale(a: argparse.Namespace) -> None:
    """Re-export an existing run with a scale reference (same dense layer as before). A bad
    reference is refused before anything in the run changes."""
    import pycolmap

    from .export import frames_from_json
    from .scale import load_reference, triangulate_reference

    run, ref = Path(a.run).resolve(), Path(a.ref).resolve()
    sparse = run / "colmap/sparse/0"
    if not sparse.is_dir() or not (run / "frames.json").is_file():
        raise StageError(f"{run} is not a reconstructed run (no colmap/sparse/0 or frames.json)")
    frames = frames_from_json(run)
    triangulate_reference(pycolmap.Reconstruction(str(sparse)), load_reference(ref),
                          {f["name"] for f in frames})  # fmt: skip
    prev = next((s for s in read_run(run) if s["name"] == "export"), {})
    dense = prev.get("params", {}).get("dense")
    source = json.loads((run / "frames.json").read_text())["source"]
    _export(run, sparse, frames, source, Path(dense) if dense else None, ref)


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
    _export(out, Path(a.sparse), frames, source, Path(a.dense).resolve() if a.dense else None)


def _frames(a: argparse.Namespace, out: Path) -> dict[str, Any]:
    video = Path(a.video).resolve()
    keys = KeyframeParams(mode=a.keyframes)
    params = {"video": video.name, "max_frames": a.max_frames, "keyframes": keys.label()}
    res = run_stage(
        out, "frames", f"ffmpeg {_ffmpeg_version()}", params,
        lambda: frames_stage.run(video, out, a.max_frames, keys),
    )  # fmt: skip
    print(quality.text(json.loads((out / "quality_report.json").read_text())))
    print(f"report: {out / 'quality_report.json'}")
    return res.metrics


def cmd_quality(a: argparse.Namespace) -> None:
    out = Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    _frames(a, out)


def cmd_run(a: argparse.Namespace) -> None:
    import pycolmap

    from .export import frames_from_json
    from .reconstruction import ColmapReconstructor

    out = Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    if a.video:
        _frames(a, out)
    else:
        run_stage(
            out, "frames", "link", {"images": str(a.images)},
            lambda: frames_stage.from_images(Path(a.images).resolve(), out),
        )  # fmt: skip
    fj = json.loads((out / "frames.json").read_text())
    names = [Path(f["file"]).name for f in fj["frames"] if f["kept"]]
    # bridges = quality drops only. The denser parallax candidates must not add merge
    # matching time: there at most MAX_DROP of the keyframes. The fixed grid stays as it was
    # (decide_drops already keeps drops <= MAX_DROP of all frames).
    dropped = [Path(f["file"]).name for f in fj["frames"]
               if not f["kept"] and f["drop_reason"] != "not_keyframe"]  # fmt: skip
    if any(f["drop_reason"] == "not_keyframe" for f in fj["frames"]):
        dropped = subsample(dropped, max(1, round(frames_stage.MAX_DROP * len(names))))
    rc = ColmapReconstructor(
        mode="video" if a.video else "images",
        max_image_size=a.max_image_size,
        max_num_features=a.max_features,
        peak_threshold=a.peak_threshold,
        mapper=a.mapper,
        merge=a.merge,
        sky_mask=a.sky_mask,
    )
    res = run_stage(
        out, "reconstruction", f"pycolmap {pycolmap.__version__}", rc.params(),
        lambda: rc.reconstruct(out / "frames", names, out / "colmap", dropped),
    )  # fmt: skip
    print("reconstruction:", res.metrics)
    ref = Path(a.scale_ref).resolve() if a.scale_ref else None
    if ref is None:  # a full run starts clean: no reference from an earlier run applies
        (out / SCALE_REF).unlink(missing_ok=True)
    _export(out, out / "colmap/sparse/0", frames_from_json(out), fj["source"], ref=ref)


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


def cmd_dense_remote(a: argparse.Namespace) -> None:
    import dataclasses
    import tempfile
    from datetime import UTC, datetime

    from .dense_fake import FakeClock, FakeGpuProvider
    from .dense_remote import GpuLedger, RemoteDenseExecutor, load_dense_remote_config

    out = Path(a.out).resolve()
    # The fake spends nothing: it may run while dense_remote is disabled, and books its hours
    # in a ledger next to its output instead of the real one.
    cfg = dataclasses.replace(load_dense_remote_config(Path(a.config)), enabled=True)
    with tempfile.TemporaryDirectory() as remote:
        p = FakeGpuProvider(Path(remote), FakeClock(datetime.now(UTC)))
        ply = RemoteDenseExecutor(p, cfg, GpuLedger(out / "gpu_ledger.fake.json"), p.clock).run(
            Path(a.frames), Path(a.sparse), out
        )
    for call in p.calls:
        print(" ".join(call))
    print("dense-remote:", ply)


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
    r.add_argument("--keyframes", choices=MODES, default="parallax",
                   help="parallax: skip frames with no camera motion; fixed: all")  # fmt: skip
    r.add_argument("--max-image-size", type=int, default=1600)
    r.add_argument("--max-features", type=int, help="SIFT features per image (default 8192)")
    r.add_argument("--peak-threshold", type=float, help="SIFT peak threshold (default 0.00667)")
    r.add_argument("--mapper", choices=["incremental", "global"], default="incremental")
    r.add_argument("--no-merge", dest="merge", action="store_false",
                   help="largest model only, no leftover registration (pre-P3)")  # fmt: skip
    r.add_argument("--no-sky-mask", dest="sky_mask", action="store_false",
                   help="extract features in the sky too (pre-P3b, ADR 0013)")  # fmt: skip
    r.add_argument("--scale-ref", metavar="JSON", help="A4 sheet or known distance marked in "
                   "frames: metric map (docs/notes/scale-reference-format.md)")  # fmt: skip
    r.set_defaults(fn=cmd_run)
    sc = sub.add_parser("scale", help="re-export a run in metres from a scale reference")
    sc.add_argument("--run", required=True)
    sc.add_argument("--ref", required=True, metavar="JSON")
    sc.set_defaults(fn=cmd_scale)
    e = sub.add_parser("export", help="COLMAP model -> LAZ/PLY + viewer bundle")
    e.add_argument("--sparse", required=True)
    e.add_argument("--images", help="image dir (image-set input); omit to use <out>/frames.json")
    e.add_argument("--out", required=True)
    e.add_argument("--dense", help="dir with fused.ply (+ fused.ply.vis): dense layer + frame "
                   "links (ADR 0017)")  # fmt: skip
    e.set_defaults(fn=cmd_export)
    q = sub.add_parser("quality", help="video -> frames + <out>/quality_report.json only")
    q.add_argument("--video", required=True)
    q.add_argument("--out", required=True)
    q.add_argument("--max-frames", type=int, help="upper cap on extracted frames (default 300)")
    q.add_argument("--keyframes", choices=MODES, default="parallax")
    q.set_defaults(fn=cmd_quality)
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
    dr = sub.add_parser("dense-remote", help="COLMAP dense on a remote GPU worker (ADR 0016)")
    dr.add_argument("--provider", choices=["fake"], required=True, help="only fake exists yet")
    dr.add_argument("--frames", required=True)
    dr.add_argument("--sparse", required=True)
    dr.add_argument("--out", required=True)
    dr.add_argument("--config", default=str(Path(__file__).resolve().parent.parent / "config.yaml"))
    dr.set_defaults(fn=cmd_dense_remote)
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
