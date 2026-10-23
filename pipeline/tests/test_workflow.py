"""Workflow steps: idempotence by step.json, force, crash recovery, from_step, the layout
helpers' legacy fallback, the step list from config.yaml. Fake in-process steps count calls."""

import json
from pathlib import Path
from typing import Any

import pytest

from qarau_pipeline import layout, workflow
from qarau_pipeline.cli import main
from qarau_pipeline.interfaces import StageError
from qarau_pipeline.workflow import Step, StepContext, StepResult, run_step, run_steps

NAMES = ["probe", "frames", "sparse", "export"]


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

    # A process killed mid-step leaves no marker at all: same outcome.
    (tmp_path / "sparse/step.json").unlink()
    c2 = Counting()
    run_steps(tmp_path, c2.steps())
    assert c2.calls == {"probe": 0, "frames": 0, "sparse": 1, "export": 1}
    assert marker(tmp_path, "sparse")["status"] == "done"


def test_from_step_runs_exactly_that_slice(tmp_path: Path) -> None:
    c = Counting()
    run_steps(tmp_path, c.steps())
    seen: list[tuple[str, str, str | None]] = []
    c2 = Counting()
    res = run_steps(
        tmp_path, c2.steps(), from_step="sparse", force=True,
        on_event=lambda name, phase, r: seen.append((name, phase, r.status if r else None)),
    )  # fmt: skip
    assert list(res) == ["sparse", "export"]
    assert c2.calls == {"probe": 0, "frames": 0, "sparse": 1, "export": 1}
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
