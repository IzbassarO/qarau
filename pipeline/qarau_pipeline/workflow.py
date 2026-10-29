"""Workflow steps (ADR 0022; contract in docs/architecture.md, "Workflow steps").

A step is a plain callable `step(ctx) -> StepResult`. It reads its inputs from the run dir,
writes its outputs under `<run>/<step>/` and finishes with `<run>/<step>/step.json` (written
atomically). A step whose marker says `done` is a no-op unless `ctx.force`; a step that crashes
leaves no marker, so the next run redoes exactly that step. Steps know nothing about queues: the
RQ runner in backend/ (and Temporal later) only calls `python -m qarau_pipeline step <name>`.

Product steps: probe -> frames (+ quality report) -> sparse (COLMAP) -> export -> coverage
(regions of the sparse map, COV1) [-> dense, only when config.yaml `workflow.dense` is true;
fake provider only, never a paid run].

Storage (ADR 0024): with `QARAU_S3_BUCKET` set, the run dir is a cache of
`<org>/<site>/<inspection>/{video,frames,sparse,dense,bundle}/` in S3. Before a step runs, the
kinds it reads and its own kind are downloaded when the local dir has no done marker (a wiped
cache is refilled, not recomputed); after its marker is written, the kinds it made are uploaded.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from . import layout
from .interfaces import StageError
from .storage import Storage, local_prefix, storage_from_env

CONFIG = Path(__file__).resolve().parent.parent / "config.yaml"
MARKER = "step.json"
WORKFLOW_JSON = "workflow.json"  # `run` records its input and parameters here for later `step`s
# Storage kinds a step reads (pulled on a cache miss) and the kinds it writes (pushed when done).
READS = {"probe": ("video",), "frames": ("video",), "sparse": ("frames",),
         "export": ("frames", "sparse"), "dense": ("frames", "sparse"),
         "coverage": ("frames", "bundle")}  # fmt: skip
# ponytail: coverage/ is not a storage kind (like findings/): a wiped cache recomputes it; the
# database keeps its regions (COV1-b) and the bundle its per-point array, so add a kind only
# when something needs regions.json itself from storage.
WRITES = {"probe": ("video",), "frames": ("frames",), "sparse": ("sparse",),
          "export": ("bundle",), "dense": ("dense", "bundle"),
          "coverage": ("bundle",)}  # fmt: skip

Log = Callable[[str], None]
OnEvent = Callable[[str, str, "StepResult | None"], None]


@dataclass
class StepContext:
    run_dir: Path
    config: dict[str, Any] = field(default_factory=dict)
    force: bool = False
    log: Log = print
    storage: Storage | None = None  # None: local files only (benchmark, tests)
    prefix: str = ""  # <org>/<site>/<inspection> in the bucket


def make_context(
    run_dir: Path, config: dict[str, Any] | None = None, force: bool = False, log: Log = print
) -> StepContext:
    """A context with the storage from the environment (if any) and the run's prefix:
    config['storage_prefix'] (the worker passes the catalog ids) or `_local/_local/<run>`."""
    cfg = config or {}
    prefix = cfg.get("storage_prefix") or local_prefix(run_dir.name)
    return StepContext(run_dir, cfg, force, log, storage_from_env(), prefix)


@dataclass
class StepResult:
    status: str  # ok | skipped | failed
    outputs: list[str] = field(default_factory=list)  # paths relative to the run dir
    metrics: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


@dataclass(frozen=True)
class Step:
    name: str
    fn: Callable[[StepContext], StepResult]


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# ---------------------------------------------------------------- markers and the runner


def marker_path(run_dir: Path, name: str) -> Path:
    return run_dir / name / MARKER


def read_marker(run_dir: Path, name: str) -> dict[str, Any] | None:
    p = marker_path(run_dir, name)
    if not p.is_file():
        return None
    try:
        doc: dict[str, Any] = json.loads(p.read_text())
    except ValueError:
        return None  # not ours or half-written: never counts as done
    return doc


def is_done(run_dir: Path, name: str) -> bool:
    m = read_marker(run_dir, name)
    return m is not None and m.get("status") == "done"


def write_marker(run_dir: Path, name: str, doc: dict[str, Any]) -> None:
    """Temp file + os.replace: a reader sees the old marker or the new one, never a partial one."""
    p = marker_path(run_dir, name)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{MARKER}.tmp")
    tmp.write_text(json.dumps(doc, indent=1))
    os.replace(tmp, p)


def run_step(step: Step, ctx: StepContext) -> StepResult:
    """One step: skipped when its marker says done (unless ctx.force); otherwise `<run>/<step>/`
    is cleared (a half-written output of a killed process never leaks into the redo), the body
    runs, and the marker records done or failed. A crash between the two leaves no marker.
    With storage: inputs and the step's own kind are pulled first, outputs pushed after."""
    if ctx.storage is not None:
        own = tuple(k for k in WRITES.get(step.name, ()) if k != "video")
        for kind in READS.get(step.name, ()) + own:
            pull(ctx, kind)
    old = read_marker(ctx.run_dir, step.name)
    if old is not None and old.get("status") == "done" and not ctx.force:
        return StepResult("skipped", list(old.get("outputs", [])), dict(old.get("metrics", {})))
    shutil.rmtree(ctx.run_dir / step.name, ignore_errors=True)
    (ctx.run_dir / step.name).mkdir(parents=True, exist_ok=True)
    started, t0 = _now(), time.perf_counter()
    try:
        res = step.fn(ctx)
    except Exception as e:  # noqa: BLE001 - recorded in the marker; the caller decides
        res = StepResult("failed", error=f"{type(e).__name__}: {e}")

    def marker(r: StepResult) -> None:
        write_marker(
            ctx.run_dir,
            step.name,
            {
                "name": step.name,
                "status": "failed" if r.status == "failed" else "done",
                "started_at": started,
                "finished_at": _now(),
                "duration_s": round(time.perf_counter() - t0, 2),
                "error": r.error,
                "outputs": r.outputs,
                "metrics": r.metrics,
            },
        )

    marker(res)  # written first, so the pushed kind carries its done marker
    if res.status != "failed" and ctx.storage is not None:
        try:
            for kind in WRITES.get(step.name, ()):
                push(ctx, kind)
        except Exception as e:  # noqa: BLE001 - an output that never reached storage is a failure
            res = StepResult("failed", error=f"upload: {type(e).__name__}: {e}")
            marker(res)
    return res


# ---------------------------------------------------------------- storage: pull and push


def _video_ext(key: str) -> str:
    return Path(key).suffix.lstrip(".").lower() or "bin"


def pull(ctx: StepContext, kind: str) -> None:
    """Download `kind` into the run dir when the cache lacks it: the video when none is here,
    a step's kind when its done marker is missing (the marker comes along, so the step is then
    skipped). No objects in storage: nothing to do."""
    st, run, prefix = ctx.storage, ctx.run_dir, f"{ctx.prefix}/{kind}/"
    assert st is not None
    if kind == "video":
        if find_video(ctx) is None and (keys := st.list(prefix)):
            st.get(keys[0], run / f"video.{_video_ext(keys[0])}")
            ctx.log(f"storage: pulled {keys[0]}")
        return
    if is_done(run, layout.KIND_STEPS[kind]):
        return
    keys = st.list(prefix)
    for key in keys:
        st.get(key, layout.kind_dir(run, kind) / key[len(prefix) :])
    if keys:
        ctx.log(f"storage: pulled {len(keys)} objects from {prefix}")


def push(ctx: StepContext, kind: str) -> None:
    """Upload the run's `kind`. The video goes up once (immutable); a rebuilt kind replaces its
    prefix, so no stale object survives a rerun."""
    st, run, prefix = ctx.storage, ctx.run_dir, f"{ctx.prefix}/{kind}/"
    assert st is not None
    if kind == "video":
        video = find_video(ctx)
        if video is not None and not st.list(prefix):
            st.put(prefix + video.name, video)
            ctx.log(f"storage: pushed {prefix}{video.name}")
        return
    d = layout.kind_dir(run, kind)
    old, new = set(st.list(prefix)), set()
    for f in sorted(p for p in d.rglob("*") if p.is_file()):
        key = prefix + f.relative_to(d).as_posix()
        st.put(key, f)
        new.add(key)
    for key in old - new:
        st.delete(key)
    ctx.log(f"storage: pushed {len(new)} objects to {prefix}")


def run_steps(
    run_dir: Path,
    steps: list[Step],
    from_step: str | None = None,
    force: bool = False,
    on_event: OnEvent | None = None,
    config: dict[str, Any] | None = None,
    log: Log = print,
) -> dict[str, StepResult]:
    """Run `steps` in order (from `from_step` on, when given). `force` reruns finished steps of
    that slice. `on_event(name, "start" | "end", result)` is called around every step. Stops at
    the first failed step with a StageError; returns the results by step name otherwise."""
    names = [s.name for s in steps]
    if from_step is not None:
        if from_step not in names:
            raise StageError(f"unknown step {from_step!r}; steps: {', '.join(names)}")
        steps = steps[names.index(from_step) :]
    ctx = make_context(run_dir, config, force, log)
    results: dict[str, StepResult] = {}
    for step in steps:
        if on_event:
            on_event(step.name, "start", None)
        res = results[step.name] = run_step(step, ctx)
        if on_event:
            on_event(step.name, "end", res)
        if res.status == "failed":
            raise StageError(f"step '{step.name}' failed: {res.error}")
    return results


# ---------------------------------------------------------------- configuration


def load_config(path: Path = CONFIG) -> dict[str, Any]:
    raw = yaml.safe_load(path.read_text()) if path.is_file() else {}
    return raw if isinstance(raw, dict) else {}


def run_config(run_dir: Path | None, config_path: Path = CONFIG, **over: Any) -> dict[str, Any]:
    """config.yaml sections + <run>/workflow.json (what `run` was called with) + overrides."""
    cfg = load_config(config_path)
    cfg["config_path"] = str(config_path)
    wf = run_dir / WORKFLOW_JSON if run_dir else None
    if wf is not None and wf.is_file():
        cfg.update(json.loads(wf.read_text()))
    cfg.update({k: v for k, v in over.items() if v is not None})
    return cfg


def write_workflow_json(run_dir: Path, params: dict[str, Any]) -> None:
    """What `run` was called with (input path, frame and COLMAP parameters), minus unset values."""
    doc = {k: v for k, v in params.items() if v is not None}
    (run_dir / WORKFLOW_JSON).write_text(json.dumps(doc, indent=1))


def dense_enabled(config: dict[str, Any]) -> bool:
    wf = config.get("workflow")
    return bool(wf.get("dense")) if isinstance(wf, dict) else False


def product_steps(config: dict[str, Any]) -> list[Step]:
    steps = [Step("probe", probe), Step("frames", frames), Step("sparse", sparse),
             Step("export", export), Step("coverage", coverage)]  # fmt: skip
    if dense_enabled(config):
        steps.append(Step("dense", dense))
    return steps


# ---------------------------------------------------------------- product steps


def _rel(run: Path, p: Path) -> str:
    return str(p.relative_to(run)) if p.is_relative_to(run) else str(p)


def find_video(ctx: StepContext) -> Path | None:
    """config['video'] (absolute, or relative to the run dir) or the uploaded <run>/video.<ext>.
    A configured path that is gone (a moved sample) falls back to the pulled <run>/video.<ext>."""
    v = ctx.config.get("video")
    if v:
        p = Path(v) if Path(v).is_absolute() else ctx.run_dir / v
        if p.is_file() or ctx.storage is None:
            return p
    hits = sorted(p for p in ctx.run_dir.glob("video.*") if p.is_file() and p.suffix != ".part")
    return hits[0] if hits else None


def probe(ctx: StepContext) -> StepResult:
    """ffprobe the input -> probe/probe.json (codec, size, rotation, HDR, fps, duration)."""
    from .probe import probe_video

    out = layout.probe_dir(ctx.run_dir) / "probe.json"
    images = ctx.config.get("images")
    if images:
        doc: dict[str, Any] = {"kind": "images", "images": str(Path(images).resolve())}
    else:
        video = find_video(ctx)
        if video is None:
            raise StageError("no input video: upload one as <run>/video.<ext> or pass --video")
        info = probe_video(video)
        doc = {"kind": "video", "video": _rel(ctx.run_dir, video), **asdict(info)}
        ctx.log(
            f"probe: {info.codec} {info.display_width}x{info.display_height} "
            f"{info.duration_s:.1f} s {info.avg_fps:.3g} fps"
            + (" HDR" if info.is_hdr else "")
            + (" VFR" if info.is_vfr else "")
            + (f" rotated {info.rotation_deg}" if info.rotation_deg else "")
        )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=1))
    keys = ("kind", "codec", "display_width", "display_height", "duration_s", "avg_fps",
            "is_hdr", "is_vfr", "rotation_deg")  # fmt: skip
    return StepResult("ok", [_rel(ctx.run_dir, out)], {k: doc[k] for k in keys if k in doc})


def _probe_doc(ctx: StepContext) -> dict[str, Any]:
    p = layout.probe_dir(ctx.run_dir) / "probe.json"
    if not p.is_file():
        raise StageError("the probe step has not run: no probe/probe.json")
    doc: dict[str, Any] = json.loads(p.read_text())
    return doc


def frames(ctx: StepContext) -> StepResult:
    """Keyframes + blur/exposure scores + the footage quality report (frames.py, quality.py)."""
    from . import frames as frames_stage
    from . import quality
    from .interfaces import run_stage
    from .keyframes import KeyframeParams

    run, doc = ctx.run_dir, _probe_doc(ctx)
    if doc["kind"] == "images":
        images = Path(doc["images"])
        res = run_stage(run, "frames", "link", {"images": str(images)},
                        lambda: frames_stage.from_images(images, run))  # fmt: skip
    else:
        video = run / doc["video"] if not Path(doc["video"]).is_absolute() else Path(doc["video"])
        keys = KeyframeParams(mode=ctx.config.get("keyframes", "parallax"))
        max_frames = ctx.config.get("max_frames")
        params = {"video": video.name, "max_frames": max_frames, "keyframes": keys.label()}
        res = run_stage(run, "frames", f"ffmpeg {frames_stage.ffmpeg_version()}", params,
                        lambda: frames_stage.run(video, run, max_frames, keys))  # fmt: skip
        rep = layout.quality_report(run)
        ctx.log(quality.text(json.loads(rep.read_text())))
        ctx.log(f"report: {rep}")
    outs = [_rel(run, layout.frames_dir(run)), _rel(run, layout.frames_json(run))]
    if doc["kind"] != "images":
        outs.append(_rel(run, layout.quality_report(run)))
    return StepResult("ok", outs, res.metrics)


def sparse(ctx: StepContext) -> StepResult:
    """COLMAP sparse model of the kept frames -> sparse/ (recorded as 'reconstruction')."""
    import pycolmap

    from . import frames as frames_stage
    from .interfaces import run_stage
    from .keyframes import subsample
    from .reconstruction import ColmapReconstructor

    run, c = ctx.run_dir, ctx.config
    fj = json.loads(layout.frames_json(run).read_text())
    names = [Path(f["file"]).name for f in fj["frames"] if f["kept"]]
    # bridges = quality drops only. The denser parallax candidates must not add merge
    # matching time: there at most MAX_DROP of the keyframes. The fixed grid stays as it was
    # (decide_drops already keeps drops <= MAX_DROP of all frames).
    dropped = [Path(f["file"]).name for f in fj["frames"]
               if not f["kept"] and f["drop_reason"] != "not_keyframe"]  # fmt: skip
    if any(f["drop_reason"] == "not_keyframe" for f in fj["frames"]):
        dropped = subsample(dropped, max(1, round(frames_stage.MAX_DROP * len(names))))
    rc = ColmapReconstructor(
        mode="video" if fj["source"]["kind"] == "video" else "images",
        max_image_size=c.get("max_image_size", 1600),
        max_num_features=c.get("max_features"),
        peak_threshold=c.get("peak_threshold"),
        mapper=c.get("mapper", "incremental"),
        merge=c.get("merge", True),
        sky_mask=c.get("sky_mask", True),
    )
    out = layout.sparse_dir(run)
    res = run_stage(
        run, "reconstruction", f"pycolmap {pycolmap.__version__}", rc.params(),
        lambda: rc.reconstruct(layout.frames_dir(run), names, out, dropped),
    )  # fmt: skip
    ctx.log(f"reconstruction: {res.metrics}")
    return StepResult("ok", [_rel(run, out)], res.metrics)


def export_run(
    run: Path,
    sparse_model: Path,
    frames_list: list[dict[str, Any]],
    source: dict[str, Any],
    dense_dir: Path | None = None,
) -> dict[str, Any]:
    """The export stage of a run (recorded in run.json as 'export'): the viewer bundle, LAZ and
    PLY under layout.export_dir(run). Returns the export metrics."""
    import pycolmap

    from .export import export_bundle
    from .interfaces import run_stage

    def go() -> dict[str, Any]:
        rec = pycolmap.Reconstruction(str(sparse_model))
        dp = None
        if dense_dir is not None:
            from .visibility import load_dense, max_frames_per_point

            dp = load_dense(dense_dir, rec, frames_list, max_frames_per_point())
        return export_bundle(rec, frames_list, run, source, run.name, dp)

    params = {"sparse": str(sparse_model), **({"dense": str(dense_dir)} if dense_dir else {})}
    res = run_stage(run, "export", f"pycolmap {pycolmap.__version__}", params, go)
    print("export:", res.metrics)
    return res.metrics


def export(ctx: StepContext) -> StepResult:
    """Sparse model + frames.json -> export/ (web bundle, cloud.laz, cloud.ply)."""
    from .export import frames_from_json

    run = ctx.run_dir
    source = json.loads(layout.frames_json(run).read_text())["source"]
    metrics = export_run(run, layout.sparse_model(run), frames_from_json(run), source)
    return StepResult("ok", [_rel(run, layout.export_dir(run))], metrics)


def dense(ctx: StepContext) -> StepResult:
    """Dense points with the FAKE provider only (synthetic, marked fake, ADR 0017) -> dense/,
    then the bundle is re-exported with the dense layer. A paid Modal run is never started from
    the workflow: `dense-remote --provider modal --allow-paid-gpu` by hand (ADR 0019)."""
    import dataclasses
    import tempfile

    from .dense_fake import FakeClock, FakeGpuProvider
    from .dense_remote import GpuLedger, RemoteDenseExecutor, load_dense_remote_config
    from .export import frames_from_json

    run = ctx.run_dir
    section = ctx.config.get("dense_remote") or {}
    if section.get("provider", "fake") != "fake":
        raise StageError(
            "the workflow's dense step runs only the fake provider; a paid GPU run is started by "
            "hand with `dense-remote --provider modal --allow-paid-gpu` (ADR 0019)"
        )
    cfg = load_dense_remote_config(Path(ctx.config.get("config_path", CONFIG)))
    cfg = dataclasses.replace(cfg, enabled=True, provider="fake")
    out = layout.dense_dir(run)
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as remote:
        p = FakeGpuProvider(Path(remote), FakeClock(datetime.now(UTC)))
        ply = RemoteDenseExecutor(p, cfg, GpuLedger(out / "gpu_ledger.fake.json"), p.clock).run(
            layout.frames_dir(run), layout.sparse_model(run), out
        )
    source = json.loads(layout.frames_json(run).read_text())["source"]
    metrics = export_run(run, layout.sparse_model(run), frames_from_json(run), source, out)
    return StepResult("ok", [_rel(run, out)], {"fused": _rel(run, ply), **metrics})


def coverage(ctx: StepContext) -> StepResult:
    """Coverage state per map region (coverage.py, COV1) -> coverage/regions.json; voxel size
    and thresholds from config.yaml `coverage:`. Then the bundle gets its per-point coverage
    array (export.write_coverage_layer, COV1-b), so the step also writes the `bundle` kind."""
    from .coverage import run_coverage
    from .export import write_coverage_layer

    metrics = run_coverage(ctx.run_dir, ctx.config)
    entry = write_coverage_layer(ctx.run_dir)
    ctx.log(f"coverage: {metrics}")
    return StepResult("ok", ["coverage/regions.json", entry["path"]], metrics)
