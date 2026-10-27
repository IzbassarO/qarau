"""CLI: python -m qarau_pipeline {run,step,quality,export,segment,project,dense-remote,
finding-evidence,report,benchmark,refresh} ..."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import layout
from .cameras import project
from .interfaces import StageError
from .keyframes import MODES
from .segmentation import GroundedSam2, ManualMasks, SamApiSegmenter, parse_prompt, segment_stage
from .workflow import (
    export_run,
    make_context,
    product_steps,
    run_config,
    run_step,
    run_steps,
    write_workflow_json,
)

# pycolmap is imported inside the commands that need it: it bundles its own libomp, which makes
# torch abort in the same process (segment stage), so the segment path must not load it.


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
        source = json.loads(layout.frames_json(out).read_text())["source"]
    dense = Path(a.dense).resolve() if a.dense else None
    export_run(out, Path(a.sparse), frames, source, dense)


def cmd_quality(a: argparse.Namespace) -> None:
    """probe + frames steps only, always redone (an explicit request for a fresh report)."""
    out = Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    cfg = run_config(out, video=str(Path(a.video).resolve()), max_frames=a.max_frames,
                     keyframes=a.keyframes)  # fmt: skip
    steps = [s for s in product_steps(cfg) if s.name in ("probe", "frames")]
    run_steps(out, steps, force=True, config=cfg)


def cmd_run(a: argparse.Namespace) -> None:
    """The whole workflow (workflow.product_steps) on one input; finished steps are skipped
    unless --force. Parameters are kept in <run>/workflow.json for later `step` reruns."""
    out = Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    params: dict[str, Any] = {
        "video": str(Path(a.video).resolve()) if a.video else None,
        "images": str(Path(a.images).resolve()) if a.images else None,
        "max_frames": a.max_frames,
        "keyframes": a.keyframes,
        "max_image_size": a.max_image_size,
        "max_features": a.max_features,
        "peak_threshold": a.peak_threshold,
        "mapper": a.mapper,
        "merge": a.merge,
        "sky_mask": a.sky_mask,
    }
    write_workflow_json(out, params)
    cfg = run_config(out)
    run_steps(out, product_steps(cfg), force=a.force, config=cfg)


def cmd_step(a: argparse.Namespace) -> None:
    """Exactly one step of a run (the queue worker's entry point); exit 1 when it fails."""
    run = Path(a.run).resolve() if a.run else None
    cfg = run_config(run, storage_prefix=a.prefix)
    steps = product_steps(cfg)
    if a.list:
        print(json.dumps([s.name for s in steps]))
        return
    if run is None or a.name is None:
        raise StageError("step needs a step name and --run DIR (or --list)")
    step = next((s for s in steps if s.name == a.name), None)
    if step is None:
        raise StageError(f"unknown step {a.name!r}; steps: {', '.join(s.name for s in steps)}")
    res = run_step(step, make_context(run, cfg, a.force))
    print(f"step {a.name}: {res.status}" + (f" ({res.error})" if res.error else ""))
    if res.status == "failed":
        sys.exit(1)


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
    cfg = load_dense_remote_config(Path(a.config))
    if a.provider == "modal":
        # Paid (ADR 0019): enabled only in memory, only behind --allow-paid-gpu (checked in
        # main); the real ledger, budget and timeout from config.yaml apply.
        from . import dense_remote

        cfg = dataclasses.replace(cfg, enabled=True, provider="modal")
        provider = dense_remote.make_provider(cfg)
        ply = RemoteDenseExecutor(provider, cfg, GpuLedger(cfg.ledger_path)).run(
            Path(a.frames), Path(a.sparse), out
        )
        print("dense-remote:", ply)
        return
    # The fake spends nothing: it may run while dense_remote is disabled, and books its hours
    # in a ledger next to its output instead of the real one.
    cfg = dataclasses.replace(cfg, enabled=True)
    with tempfile.TemporaryDirectory() as remote:
        p = FakeGpuProvider(Path(remote), FakeClock(datetime.now(UTC)))
        ply = RemoteDenseExecutor(p, cfg, GpuLedger(out / "gpu_ledger.fake.json"), p.clock).run(
            Path(a.frames), Path(a.sparse), out
        )
    for call in p.calls:
        print(" ".join(call))
    print("dense-remote:", ply)


def cmd_finding_evidence(a: argparse.Namespace) -> None:
    import cv2
    import numpy as np

    from .findings import evidence_for, load_bundle, region_area, select_points

    run, out = Path(a.run).resolve(), Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    bundle = load_bundle(run)
    try:
        sel = select_points(bundle.positions, json.loads(a.polygon))
    except ValueError as err:
        raise StageError(str(err)) from err
    if not len(sel):
        raise StageError("the polygon selects no points of the map")
    frames = evidence_for(bundle, sel)
    for ev in frames:
        img = cv2.imread(str(run / ev["image"]))
        if img is None:
            raise StageError(f"cannot read {ev['image']}")
        cam = bundle.cameras[ev["frame"]]
        k = np.array([img.shape[1] / cam.width, img.shape[0] / cam.height])
        xc = bundle.positions[sel] @ cam.R.T + cam.t
        for u, v in (project(cam.model, cam.params, xc[xc[:, 2] > 0]) * k).round().astype(int):
            cv2.circle(img, (int(u), int(v)), 2, (0, 255, 0), -1)
        if ev["outline"]:
            poly = (np.array(ev["outline"]) * k).round().astype(np.int32).reshape(-1, 1, 2)
            cv2.polylines(img, [poly], True, (0, 0, 255), 2)
        ev["render"] = str(out / f"frame_{ev['frame']:06d}.png")
        cv2.imwrite(ev["render"], img)
    doc = {"run": str(run), "polygon": json.loads(a.polygon), "n_points": int(len(sel)),
           "area_units": region_area(bundle.positions[sel]), "scale": "not set",
           "point_indices": sel.tolist(), "frames": frames}  # fmt: skip
    (out / "finding.json").write_text(json.dumps(doc, indent=1))
    print(f"finding-evidence: {len(sel)} points, area {doc['area_units']:.4g} units^2 "
          f"(scale not set), frames {[ev['frame'] for ev in frames]} -> {out}")  # fmt: skip


def cmd_report(a: argparse.Namespace) -> None:
    import os
    from datetime import datetime

    import sqlalchemy as sa

    from .report import generate

    url = a.db or os.environ.get("QARAU_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url:
        raise StageError("report needs --db URL or QARAU_DATABASE_URL (the API's database)")
    try:
        when = datetime.fromisoformat(a.report_time) if a.report_time else datetime.now()
    except ValueError as err:
        raise StageError(f"--report-time: {err}") from err
    # naive: the machine's zone; whole seconds, so the printed time reproduces the file
    when = (when.astimezone() if when.tzinfo is None else when).replace(microsecond=0)
    engine = sa.create_engine(url)
    try:
        pdf, sha = generate(a.inspection, a.lang, when, engine)
    finally:
        engine.dispose()
    Path(a.out).write_bytes(pdf)
    print(f"report: {a.out} ({len(pdf)} bytes) sha256 {sha} report time {when.isoformat()}")


def cmd_benchmark(a: argparse.Namespace) -> None:
    from .benchmark import run_benchmark

    only = [n for s in a.only for n in s.split(",") if n] if a.only else None
    sys.exit(run_benchmark(a.data, a.out, only, a.timeout_s, a.record))


def cmd_refresh(a: argparse.Namespace) -> None:
    from .refresh import markdown, refresh

    only = [n for s in a.only for n in s.split(",") if n] if a.only else None
    report = Path(a.report).resolve() if a.report else None
    if report and report.is_relative_to(Path(a.runs).resolve()):  # a doc, never a file in runs
        raise StageError(f"--report {report} must be outside --runs")
    res = refresh(a.data, a.runs, a.suffix, only, a.timeout_s, work_dir=a.work)
    table = markdown(res)
    print(table)
    if report:
        report.write_text(f"# Refresh report: suffix `{a.suffix}`\n\n{table}\n")
        print(f"report: {report}")
    sys.exit(1 if any(r["status"].startswith("failed") for r in res.values()) else 0)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="qarau_pipeline")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="video or image set -> probe -> frames -> COLMAP -> viewer "
                       "bundle (steps already done are skipped)")  # fmt: skip
    g = r.add_mutually_exclusive_group(required=True)
    g.add_argument("--video")
    g.add_argument("--images")
    r.add_argument("--out", required=True)
    r.add_argument("--force", action="store_true", help="redo steps whose step.json says done")
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
    r.set_defaults(fn=cmd_run)
    st = sub.add_parser("step", help="run exactly one workflow step of a run dir (ADR 0022)")
    st.add_argument("name", nargs="?", help="probe | frames | sparse | export [| dense]")
    st.add_argument("--run", help="run dir: <run>/video.<ext> or <run>/workflow.json names the "
                    "input")  # fmt: skip
    st.add_argument("--force", action="store_true", help="redo the step even if step.json is done")
    st.add_argument("--prefix", help="storage prefix <org>/<site>/<inspection> (ADR 0024); "
                    "default _local/_local/<run dir name>")  # fmt: skip
    st.add_argument("--list", action="store_true", help="print the step names as JSON and exit")
    st.set_defaults(fn=cmd_step)
    e = sub.add_parser("export", help="COLMAP model -> LAZ/PLY + viewer bundle")
    e.add_argument("--sparse", required=True)
    e.add_argument("--images", help="image dir (image-set input); omit to use <out>'s frames.json")
    e.add_argument("--out", required=True)
    e.add_argument("--dense", help="dir with fused.ply (+ fused.ply.vis): dense layer + frame "
                   "links (ADR 0017)")  # fmt: skip
    e.set_defaults(fn=cmd_export)
    q = sub.add_parser("quality", help="video -> frames + <out>/frames/quality_report.json only")
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
    dr.add_argument("--provider", choices=["fake", "modal"], default="fake",
                    help="fake (default, free) or modal (paid, needs --allow-paid-gpu); "
                    "dense_remote.provider in config.yaml does not change the default")  # fmt: skip
    dr.add_argument("--allow-paid-gpu", action="store_true",
                    help="confirm that --provider modal starts a paid Modal GPU run")  # fmt: skip
    dr.add_argument("--frames", required=True)
    dr.add_argument("--sparse", required=True)
    dr.add_argument("--out", required=True)
    dr.add_argument("--config", default=str(Path(__file__).resolve().parent.parent / "config.yaml"))
    dr.set_defaults(fn=cmd_dense_remote)
    fe = sub.add_parser("finding-evidence", help="polygon on a run's map -> the 3 best frames "
                        "with the region outlined (PNG) + finding.json (DEF1)")  # fmt: skip
    fe.add_argument("--run", required=True, help="run dir with web/manifest.json")
    fe.add_argument("--polygon", required=True, help="JSON: [[x, y, z], ...] (>= 3 vertices)")
    fe.add_argument("--out", required=True)
    fe.set_defaults(fn=cmd_finding_evidence)
    rp = sub.add_parser("report", help="inspection -> PDF report (RPT1, ADR 0027)")
    rp.add_argument("--inspection", type=int, required=True, help="inspection id in the API db")
    rp.add_argument("--lang", choices=["ru", "kk", "en"], required=True)
    rp.add_argument("--out", required=True, help="PDF file to write")
    rp.add_argument("--report-time", help="ISO 8601, e.g. 2026-10-26T14:32:00+05:00 (default: "
                    "now); the only clock in the PDF: same time, same file")  # fmt: skip
    rp.add_argument("--db", help="SQLAlchemy URL (default: $QARAU_DATABASE_URL)")
    rp.set_defaults(fn=cmd_report)
    b = sub.add_parser("benchmark", help="all reference videos -> <out>/results.json")
    b.add_argument("--data", required=True, help="dir with the reference videos (read-only)")
    b.add_argument("--out", required=True)
    b.add_argument("--only", nargs="+", metavar="NAME", help="manifest names to run (a,b or a b)")
    b.add_argument("--timeout-s", type=float, default=1800, help="per video (default 1800)")
    b.add_argument("--record", action="store_true", help="also write docs/benchmarks/<sha>.json"
                   " and append rows to docs/notes/benchmark.md")  # fmt: skip
    b.set_defaults(fn=cmd_benchmark)
    rf = sub.add_parser("refresh", help="every reference video -> sparse CPU run -> NEW "
                        "<runs>/<video>-<suffix> (never touches existing runs)")  # fmt: skip
    rf.add_argument("--data", required=True, help="dir with the reference videos (read-only)")
    rf.add_argument("--runs", required=True, help="viewer runs dir, e.g. ../data/runs")
    rf.add_argument("--suffix", required=True, help="run name = <video>-<suffix>, e.g. p3e")
    rf.add_argument("--only", nargs="+", metavar="NAME", help="manifest names to run (a,b or a b)")
    rf.add_argument("--work", help="where runs are built: outside --runs, same filesystem "
                    "(default: the system temp dir)")  # fmt: skip
    rf.add_argument("--report", help="also write the table as Markdown to this file")
    rf.add_argument("--timeout-s", type=float, default=1800, help="per video (default 1800)")
    rf.set_defaults(fn=cmd_refresh)
    a = p.parse_args(argv)
    if a.cmd == "dense-remote" and a.provider == "modal" and not a.allow_paid_gpu:
        dr.error("--provider modal starts a paid Modal GPU run; add --allow-paid-gpu to confirm")
    try:
        a.fn(a)
    except StageError as err:
        print(f"ERROR: {err}", file=sys.stderr)
        sys.exit(1)
