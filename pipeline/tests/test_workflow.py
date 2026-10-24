"""Workflow steps: idempotence by step.json, force, crash recovery, from_step, the layout
helpers' legacy fallback, the step list from config.yaml. Fake in-process steps count calls.
Storage (ADR 0024): an S1 run on a synthetic video against moto's S3 fills the five prefixes, a
wiped cache is refilled from storage, and without a bucket boto3 is never touched."""

import json
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import cv2
import pytest
import synth
from moto import mock_aws

from qarau_pipeline import layout, workflow
from qarau_pipeline.cli import main
from qarau_pipeline.interfaces import StageError
from qarau_pipeline.storage import S3Storage, storage_from_env
from qarau_pipeline.workflow import Step, StepContext, StepResult, run_step, run_steps

NAMES = ["probe", "frames", "sparse", "export"]
S3_ENV = {"QARAU_S3_BUCKET": "qarau-test", "QARAU_S3_ENDPOINT": "", "QARAU_S3_REGION": "us-east-1",
          "QARAU_S3_ACCESS_KEY": "testing", "QARAU_S3_SECRET_KEY": "testing"}  # fmt: skip
DENSE_MANIFEST = '{"version": 2, "dense": 1}'


class Counting:
    """Fake step bodies; `calls[name]` counts how often each body ran. A body in `fail` raises
    after writing a partial file."""

    def __init__(self) -> None:
        self.calls: dict[str, int] = dict.fromkeys(NAMES, 0)
        self.fail: set[str] = set()

    def step(self, name: str) -> Step:
        def body(ctx: StepContext) -> StepResult:
            self.calls[name] += 1
            (ctx.run_dir / name).mkdir(exist_ok=True)
            (ctx.run_dir / name / "partial.txt").write_text("half")
            if name in self.fail:
                raise RuntimeError(f"{name} exploded")
            (ctx.run_dir / name / "out.txt").write_text("done")
            return StepResult("ok", [f"{name}/out.txt"], {"n": self.calls[name]})

        return Step(name, body)

    def steps(self) -> list[Step]:
        return [self.step(n) for n in NAMES]


def marker(run: Path, name: str) -> dict[str, Any]:
    doc: dict[str, Any] = json.loads((run / name / "step.json").read_text())
    return doc


def test_second_run_is_a_no_op_and_force_reruns_one_step(tmp_path: Path) -> None:
    c = Counting()
    res = run_steps(tmp_path, c.steps())
    assert [r.status for r in res.values()] == ["ok", "ok", "ok", "ok"]
    assert c.calls == {"probe": 1, "frames": 1, "sparse": 1, "export": 1}
    m = marker(tmp_path, "sparse")
    assert m["name"] == "sparse" and m["status"] == "done" and m["error"] is None
    assert m["outputs"] == ["sparse/out.txt"] and m["metrics"] == {"n": 1}
    assert set(m) == {"name", "status", "started_at", "finished_at", "duration_s", "error",
                      "outputs", "metrics"}  # fmt: skip

    c2 = Counting()  # same run dir, fresh counters: every body runs 0 times
    res = run_steps(tmp_path, c2.steps())
    assert c2.calls == {"probe": 0, "frames": 0, "sparse": 0, "export": 0}
    assert [r.status for r in res.values()] == ["skipped"] * 4
    assert res["sparse"].outputs == ["sparse/out.txt"] and res["sparse"].metrics == {"n": 1}

    c3 = Counting()  # force on one step: exactly 1 call
    assert run_step(c3.step("sparse"), StepContext(tmp_path, force=True)).status == "ok"
    assert c3.calls["sparse"] == 1 and marker(tmp_path, "sparse")["metrics"] == {"n": 1}


def test_crash_leaves_no_done_marker_and_next_run_redoes_only_it(tmp_path: Path) -> None:
    c = Counting()
    c.fail.add("sparse")
    with pytest.raises(StageError, match="step 'sparse' failed: RuntimeError: sparse exploded"):
        run_steps(tmp_path, c.steps())
    assert c.calls == {"probe": 1, "frames": 1, "sparse": 1, "export": 0}
    assert (tmp_path / "sparse/partial.txt").exists()  # the partial write stays, the marker says
    m = marker(tmp_path, "sparse")
    assert m["status"] == "failed" and m["error"] == "RuntimeError: sparse exploded"
    assert not (tmp_path / "export").exists()

    # A process killed mid-step leaves no marker at all: same outcome, and the half-written
    # step dir is cleared before the redo while the finished steps keep their files.
    (tmp_path / "sparse/step.json").unlink()
    (tmp_path / "sparse/stale.bin").write_bytes(b"\0" * 8)
    c2 = Counting()
    run_steps(tmp_path, c2.steps())
    assert c2.calls == {"probe": 0, "frames": 0, "sparse": 1, "export": 1}
    assert marker(tmp_path, "sparse")["status"] == "done"
    assert sorted(p.name for p in (tmp_path / "sparse").iterdir()) == ["out.txt", "partial.txt",
                                                                        "step.json"]  # fmt: skip
    assert (tmp_path / "frames/out.txt").read_text() == "done"


def test_from_step_runs_exactly_that_slice(tmp_path: Path) -> None:
    c = Counting()
    run_steps(tmp_path, c.steps())
    untouched = {n: (tmp_path / n / "step.json").stat().st_mtime_ns for n in ("probe", "frames")}
    seen: list[tuple[str, str, str | None]] = []
    c2 = Counting()
    res = run_steps(
        tmp_path, c2.steps(), from_step="sparse", force=True,
        on_event=lambda name, phase, r: seen.append((name, phase, r.status if r else None)),
    )  # fmt: skip
    assert list(res) == ["sparse", "export"]
    assert c2.calls == {"probe": 0, "frames": 0, "sparse": 1, "export": 1}
    assert {n: (tmp_path / n / "step.json").stat().st_mtime_ns for n in untouched} == untouched
    assert marker(tmp_path, "sparse")["metrics"] == {"n": 1}  # the forced rerun wrote a new one
    assert seen == [("sparse", "start", None), ("sparse", "end", "ok"),
                    ("export", "start", None), ("export", "end", "ok")]  # fmt: skip
    with pytest.raises(StageError, match="unknown step 'nope'"):
        run_steps(tmp_path, c2.steps(), from_step="nope")


def test_marker_is_replaced_atomically_and_half_written_is_not_done(tmp_path: Path) -> None:
    workflow.write_marker(tmp_path, "probe", {"status": "done"})
    assert not list((tmp_path / "probe").glob("*.tmp"))
    assert workflow.is_done(tmp_path, "probe")
    (tmp_path / "probe/step.json").write_text('{"status": "do')
    assert not workflow.is_done(tmp_path, "probe")
    c = Counting()
    assert run_step(c.step("probe"), StepContext(tmp_path)).status == "ok" and c.calls["probe"] == 1


def test_layout_helpers_resolve_a_legacy_run(tmp_path: Path) -> None:
    legacy = tmp_path / "old"
    (legacy / "colmap/sparse/0").mkdir(parents=True)
    (legacy / "web").mkdir()
    (legacy / "web/manifest.json").write_text("{}")
    (legacy / "frames.json").write_text("{}")
    (legacy / "quality_report.json").write_text("{}")
    assert layout.sparse_dir(legacy) == legacy / "colmap"
    assert layout.sparse_model(legacy) == legacy / "colmap/sparse/0"
    assert layout.export_dir(legacy) == legacy
    assert layout.manifest_path(legacy) == legacy / "web/manifest.json"
    assert layout.frames_json(legacy) == legacy / "frames.json"
    assert layout.quality_report(legacy) == legacy / "quality_report.json"
    # A step marker alone (a rerun through the step runner) does not move the model or bundle.
    for name in ("sparse", "export"):
        (legacy / name).mkdir()
        (legacy / name / "step.json").write_text("{}")
    assert layout.sparse_dir(legacy) == legacy / "colmap"
    assert layout.export_dir(legacy) == legacy
    (legacy / "export/web").mkdir()
    (legacy / "export/web/manifest.json").write_text("{}")
    assert layout.export_dir(legacy) == legacy / "export"

    new = tmp_path / "new"  # nothing there yet: the new layout
    new.mkdir()
    assert layout.sparse_dir(new) == new / "sparse"
    assert layout.sparse_model(new) == new / "sparse/sparse/0"
    assert layout.export_dir(new) == new / "export"
    assert layout.manifest_path(new) == new / "export/web/manifest.json"
    assert layout.frames_json(new) == new / "frames/frames.json"
    assert layout.quality_report(new) == new / "frames/quality_report.json"
    assert layout.probe_dir(new) == new / "probe" and layout.dense_dir(new) == new / "dense"


def test_step_list_from_config(tmp_path: Path) -> None:
    assert [s.name for s in workflow.product_steps({})] == NAMES
    assert [s.name for s in workflow.product_steps({"workflow": {"dense": False}})] == NAMES
    assert [s.name for s in workflow.product_steps({"workflow": {"dense": True}})] == [
        *NAMES,
        "dense",
    ]
    # The checked-in config.yaml keeps dense off: the shipped workflow never densifies.
    assert workflow.dense_enabled(workflow.load_config()) is False
    cfg = tmp_path / "c.yaml"
    cfg.write_text("workflow:\n  dense: true\n")
    merged = workflow.run_config(None, cfg)
    assert merged["workflow"] == {"dense": True} and merged["config_path"] == str(cfg)


def test_dense_step_refuses_a_paid_provider(tmp_path: Path) -> None:
    ctx = StepContext(tmp_path, {"dense_remote": {"provider": "modal"}})
    with pytest.raises(StageError, match="only the fake provider"):
        workflow.dense(ctx)
    assert not (tmp_path / "dense").exists()


def test_cli_step_list_and_unknown_step(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    main(["step", "--list"])
    assert json.loads(capsys.readouterr().out) == NAMES
    with pytest.raises(SystemExit) as e:
        main(["step", "nope", "--run", str(tmp_path)])
    assert e.value.code == 1 and "unknown step 'nope'" in capsys.readouterr().err


def test_cli_step_probe_without_video_fails_with_a_marker(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as e:
        main(["step", "probe", "--run", str(tmp_path)])
    assert e.value.code == 1
    m = marker(tmp_path, "probe")
    assert m["status"] == "failed" and m["error"].startswith("StageError: no input video")


def test_run_config_merges_workflow_json(tmp_path: Path) -> None:
    params = {"video": "/v.mp4", "max_frames": None, "mapper": "global"}
    workflow.write_workflow_json(tmp_path, params)
    assert json.loads((tmp_path / "workflow.json").read_text()) == {
        "video": "/v.mp4",
        "mapper": "global",
    }
    cfg = workflow.run_config(tmp_path, keyframes="fixed")
    assert (cfg["video"], cfg["mapper"], cfg["keyframes"]) == ("/v.mp4", "global", "fixed")
    ctx = StepContext(tmp_path, {"video": "clip.webm"})
    assert workflow.find_video(ctx) == tmp_path / "clip.webm"
    (tmp_path / "video.mov.part").write_bytes(b"")
    assert workflow.find_video(StepContext(tmp_path)) is None  # an unfinished upload is not input
    (tmp_path / "video.mov").write_bytes(b"")
    assert workflow.find_video(StepContext(tmp_path)) == tmp_path / "video.mov"


# ---------------------------------------------------------------- storage (ADR 0024)


@pytest.fixture
def s3(monkeypatch: pytest.MonkeyPatch) -> Iterator[S3Storage]:
    """moto's S3 for the whole test; QARAU_S3_* set, so make_context attaches the storage."""
    for k, v in S3_ENV.items():
        monkeypatch.setenv(k, v)
    with mock_aws():
        s = storage_from_env()
        assert s is not None
        yield s


def synth_video(path: Path, n: int = 24) -> Path:
    """tests/synth.py's sideways camera over a scene with depth, as a 6 fps mp4."""
    w = cv2.VideoWriter(str(path), cv2.VideoWriter.fourcc(*"mp4v"), synth.FPS, (synth.W, synth.H))
    for img in synth.translate(n):
        w.write(cv2.cvtColor(img, cv2.COLOR_GRAY2BGR))
    w.release()
    assert path.stat().st_size > 0
    return path


class Heavy:
    """Stand-ins for the COLMAP, export and dense bodies (the storage sync is in run_step, the
    same for every body): each reads its inputs from the cache and writes the files the real
    step would. `calls` counts the runs."""

    def __init__(self) -> None:
        self.calls = {"sparse": 0, "export": 0, "dense": 0}

    def sparse(self, ctx: StepContext) -> StepResult:
        self.calls["sparse"] += 1
        fj = json.loads(layout.frames_json(ctx.run_dir).read_text())  # the cache miss path
        model = layout.sparse_model(ctx.run_dir)
        model.mkdir(parents=True)
        (model / "points3D.bin").write_bytes(b"\0" * 64)
        (layout.sparse_dir(ctx.run_dir) / "database.db").write_bytes(b"SQLite format 3\0")
        return StepResult("ok", ["sparse"], {"frames": len(fj["frames"])})

    def export(self, ctx: StepContext) -> StepResult:
        self.calls["export"] += 1
        assert (layout.sparse_model(ctx.run_dir) / "points3D.bin").is_file()
        web = layout.export_dir(ctx.run_dir) / "web"
        web.mkdir(parents=True, exist_ok=True)
        (web / "manifest.json").write_text('{"version": 2}')
        (web / "points.position.f32").write_bytes(b"\0" * 12)
        (layout.export_dir(ctx.run_dir) / "cloud.laz").write_bytes(b"LASF")
        return StepResult("ok", ["export"], {})

    def dense(self, ctx: StepContext) -> StepResult:
        self.calls["dense"] += 1
        assert (layout.sparse_model(ctx.run_dir) / "points3D.bin").is_file()
        layout.dense_dir(ctx.run_dir).mkdir(exist_ok=True)
        (layout.dense_dir(ctx.run_dir) / "fused.ply").write_bytes(b"ply\n")
        (layout.export_dir(ctx.run_dir) / "web/manifest.json").write_text(DENSE_MANIFEST)
        return StepResult("ok", ["dense"], {})

    def steps(self) -> list[Step]:
        real = {s.name: s for s in workflow.product_steps({"workflow": {"dense": True}})}
        return [real["probe"], real["frames"], Step("sparse", self.sparse),
                Step("export", self.export), Step("dense", self.dense)]  # fmt: skip


def relative(keys: list[str], prefix: str) -> list[str]:
    assert all(k.startswith(prefix) for k in keys)
    return [k[len(prefix) :] for k in keys]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_s1_run_with_storage_fills_the_five_prefixes_and_a_wiped_cache_is_refilled(
    s3: S3Storage, tmp_path: Path
) -> None:
    video = synth_video(tmp_path / "synth.mp4")
    run = tmp_path / "runs" / "syn-1"
    run.mkdir(parents=True)
    h = Heavy()
    cfg = {"video": str(video), "keyframes": "fixed", "workflow": {"dense": True}}
    res = run_steps(run, h.steps(), config=cfg)
    assert [r.status for r in res.values()] == ["ok"] * 5
    p = "_local/_local/syn-1/"  # a run outside the catalog: org _local, site _local, run name
    assert s3.list(p + "video/") == [p + "video/synth.mp4"]
    frames = relative(s3.list(p + "frames/"), p + "frames/")
    assert {
        "000000.jpg",
        "frames.json",
        "manifest.json",
        "quality_report.json",
        "step.json",
    } <= set(frames)
    assert relative(s3.list(p + "sparse/"), p + "sparse/") == [
        "database.db",
        "sparse/0/points3D.bin",
        "step.json",
    ]
    assert relative(s3.list(p + "dense/"), p + "dense/") == ["fused.ply", "step.json"]
    assert relative(s3.list(p + "bundle/"), p + "bundle/") == [
        "cloud.laz",
        "step.json",
        "web/manifest.json",
        "web/points.position.f32",
    ]
    # The dense step re-exported the bundle: storage holds the new manifest.
    assert b"".join(s3.stream(p + "bundle/web/manifest.json")) == DENSE_MANIFEST.encode()
    n_frames = len(json.loads((run / "frames/frames.json").read_text())["frames"])
    assert n_frames > 1

    # Cache miss: the frames dir is gone; a forced rerun from sparse downloads it first.
    shutil.rmtree(run / "frames")
    res = run_steps(run, h.steps(), from_step="sparse", force=True, config=cfg)
    assert [r.status for r in res.values()] == ["ok"] * 3 and h.calls["sparse"] == 2
    assert workflow.is_done(run, "frames")
    assert len(json.loads((run / "frames/frames.json").read_text())["frames"]) == n_frames
    assert len(list((run / "frames").glob("*.jpg"))) == n_frames

    # The whole cache is gone (and so is the sample video): every stored step is restored, not
    # recomputed; only probe (local, sub-second) runs again, on the video pulled from storage.
    shutil.rmtree(run)
    video.unlink()
    run.mkdir()
    before = dict(h.calls)
    res = run_steps(run, h.steps(), config=cfg)
    assert [r.status for r in res.values()] == ["ok", "skipped", "skipped", "skipped", "skipped"]
    assert h.calls == before
    assert (run / "video.mp4").is_file() and (run / "export/web/manifest.json").is_file()
    assert (run / "dense/fused.ply").is_file() and (run / "sparse/sparse/0/points3D.bin").is_file()
    assert s3.list(p + "video/") == [p + "video/synth.mp4"]  # still exactly one video object


def test_rebuilt_kind_replaces_its_prefix(s3: S3Storage, tmp_path: Path) -> None:
    """A rerun of a step deletes the objects it no longer produces; the video is never re-put."""
    (tmp_path / "video.mp4").write_bytes(b"v")
    body_calls: list[str] = []

    def body(ctx: StepContext) -> StepResult:
        body_calls.append("x")
        d = ctx.run_dir / "sparse"
        d.mkdir(exist_ok=True)
        (d / f"model-{len(body_calls)}.bin").write_bytes(b"m")
        return StepResult("ok", ["sparse"], {})

    ctx = workflow.make_context(tmp_path)
    assert ctx.storage is not None and ctx.prefix == f"_local/_local/{tmp_path.name}"
    run_step(Step("probe", lambda c: StepResult("ok")), ctx)
    run_step(Step("sparse", body), ctx)
    p = f"_local/_local/{tmp_path.name}/"
    assert s3.list(p + "sparse/") == [p + "sparse/model-1.bin", p + "sparse/step.json"]
    run_step(Step("sparse", body), workflow.make_context(tmp_path, force=True))
    assert s3.list(p + "sparse/") == [p + "sparse/model-2.bin", p + "sparse/step.json"]
    run_step(Step("probe", lambda c: StepResult("ok")), workflow.make_context(tmp_path, force=True))
    assert s3.list(p + "video/") == [p + "video/video.mp4"]


def test_failed_upload_marks_the_step_failed(s3: S3Storage, tmp_path: Path) -> None:
    class Broken(S3Storage):
        def put(self, key: str, data: Path | bytes) -> None:
            raise OSError("storage down")

    ctx = StepContext(tmp_path, storage=Broken("qarau-test"), prefix="_local/_local/r")
    (tmp_path / "video.mp4").write_bytes(b"v")
    res = run_step(Step("probe", lambda c: StepResult("ok")), ctx)
    assert res.status == "failed" and res.error == "upload: OSError: storage down"
    assert marker(tmp_path, "probe")["status"] == "failed"


def test_without_a_bucket_boto3_is_never_touched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("QARAU_S3_BUCKET", raising=False)
    monkeypatch.setattr("boto3.client", lambda *a, **k: pytest.fail("boto3.client called"))
    c = Counting()
    res = run_steps(tmp_path, c.steps())
    assert [r.status for r in res.values()] == ["ok"] * 4
    assert workflow.make_context(tmp_path).storage is None
