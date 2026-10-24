import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest
import synth

from qarau_pipeline import benchmark, dense_remote, refresh, visibility
from qarau_pipeline.cli import main
from qarau_pipeline.interfaces import StageError, run_stage

SET = """
[[video]]
name = "a"
file = "a.webm"
required = true

[[video]]
name = "b"
file = "b.webm"
required = true
max_frames = 150
"""


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Two fake videos, a runs dir with one old run and a stray summary file, run_video = real
    export of a synthetic wall model (so the bundle and its created_at come from export.py)."""
    data, runs, work = tmp_path / "data", tmp_path / "runs", tmp_path / "work"
    data.mkdir()
    for f in ("a.webm", "b.webm"):
        (data / f).write_bytes(b"not a real video")
    (runs / "old/web").mkdir(parents=True)
    (runs / "old/web/manifest.json").write_text('{"run_id": "old"}')
    (runs / "old/cloud.ply").write_bytes(b"\x00\x01ply")
    (runs / "old-link").symlink_to("old")
    (runs / "refresh-2026-10-19.json").write_text('{"left": "by round 2"}')
    manifest = tmp_path / "set.toml"
    manifest.write_text(SET)
    s = synth.wall_scene()
    sparse, images = tmp_path / "sparse", tmp_path / "images"
    synth.write_wall_model(s, sparse)
    images.mkdir()
    for i in s["views"]:
        cv2.imwrite(str(images / f"f{i}.jpg"), np.zeros((240, 320, 3), np.uint8))
    calls: dict[str, int | None] = {}
    outs: list[Path] = []
    fail: dict[str, Exception] = {}

    def fake_run_video(
        video: Path, out: Path, timeout_s: float, max_frames: int | None = None
    ) -> dict[str, Any]:
        calls[video.name] = max_frames
        outs.append(out)
        (out / "web").mkdir(parents=True, exist_ok=True)
        (out / "web/partial.f32").write_bytes(b"half")  # a run that dies mid-way
        if video.name in fail:
            raise fail[video.name]
        m = {"images_in": 8, "registered_images": len(s["views"])}
        run_stage(out, "reconstruction", "fake", {}, lambda: m)
        main(["export", "--sparse", str(sparse), "--images", str(images), "--out", str(out)])
        return m

    monkeypatch.setattr(refresh, "run_video", fake_run_video)
    return {"data": data, "runs": runs, "work": work, "manifest": manifest, "calls": calls,
            "outs": outs, "fail": fail}  # fmt: skip


def go(env: dict[str, Any], **kw: Any) -> dict[str, dict[str, Any]]:
    return refresh.refresh(
        env["data"], env["runs"], "p3e", manifest=env["manifest"], work_dir=env["work"], **kw
    )


def cli(env: dict[str, Any], *extra: str) -> None:
    main(["refresh", "--data", str(env["data"]), "--runs", str(env["runs"]), "--suffix", "p3e",
          "--work", str(env["work"]), *extra])  # fmt: skip


def snapshot(root: Path) -> dict[str, Any]:
    return {
        str(p.relative_to(root)): (
            str(p.readlink()) if p.is_symlink() else p.is_dir() or p.read_bytes(),
            p.lstat().st_mtime_ns,
        )
        for p in root.rglob("*")
    }


OLD = ["old", "old-link", "refresh-2026-10-19.json"]


def test_new_runs_next_to_old_ones(env: dict[str, Any]) -> None:
    """(b) unrelated runs and stray files untouched; only <name>-<suffix> dirs added."""
    before = snapshot(env["runs"])
    res = go(env)
    assert sorted(p.name for p in env["runs"].iterdir()) == ["a-p3e", "b-p3e", *OLD]
    after = snapshot(env["runs"])
    assert {k: after[k] for k in before} == before  # old run + stray json byte-identical, mtimes
    assert env["calls"] == {"a.webm": None, "b.webm": 150}  # per-video max_frames reused
    assert all(not o.is_relative_to(env["runs"]) for o in env["outs"])  # built outside runs
    assert list(env["work"].iterdir()) == []  # temp dirs cleaned up
    r = res["b"]
    assert r["status"] == "ok" and r["run"] == "b-p3e"
    assert r["keyframes"] == 8 and r["registered_pct"] == 75.0 and r["time_s"] >= 0
    m = json.loads((env["runs"] / "b-p3e/export/web/manifest.json").read_text())
    assert m["run_id"] == "b-p3e"  # export named it: built under its final name
    assert r["points"] == m["stats"]["points"] > 0 and r["far_points"] == m["stats"]["far_points"]
    assert "| b | b-p3e | 75.0 |" in refresh.markdown(res)


def test_created_at_is_utc_iso(env: dict[str, Any]) -> None:
    """(c) created_at written at export: parseable, UTC, recent."""
    t0 = datetime.now(UTC).replace(microsecond=0)
    go(env, only=["a"])
    m = json.loads((env["runs"] / "a-p3e/export/web/manifest.json").read_text())
    created = datetime.fromisoformat(m["created_at"])
    assert created.utcoffset() == timedelta(0)
    assert t0 <= created <= datetime.now(UTC)  # taken at export, not copied


def test_existing_run_refused_byte_identical(env: dict[str, Any]) -> None:
    """(a) a run dir with the target name: that video fails, its files are unchanged."""
    (env["runs"] / "b-p3e/web").mkdir(parents=True)
    (env["runs"] / "b-p3e/web/manifest.json").write_text('{"keep": "me"}')
    (env["runs"] / "a-p3e").symlink_to("missing-target")  # dangling link counts too
    before = snapshot(env["runs"])
    res = go(env)
    assert snapshot(env["runs"]) == before and env["calls"] == {}  # nothing run, nothing written
    status = "failed: run already exists, pick another --suffix"
    for n in ("a", "b"):
        assert res[n] == {**dict.fromkeys(refresh.COLS), "run": f"{n}-p3e", "status": status}


def test_failure_reported_and_next_video_exported(env: dict[str, Any]) -> None:
    """(d) one video fails with a reason, the next one still exports."""
    env["fail"]["a.webm"] = StageError("exit 1: no registered images")
    res = go(env)
    assert res["a"] == {**dict.fromkeys(refresh.COLS), "run": "a-p3e",
                        "status": "failed: StageError: exit 1: no registered images"}  # fmt: skip
    assert res["b"]["status"] == "ok" and res["b"]["points"] > 0
    assert sorted(p.name for p in env["runs"].iterdir()) == ["b-p3e", *OLD]  # no partial a
    assert list(env["work"].iterdir()) == []


def test_timeout_reported(env: dict[str, Any]) -> None:
    env["fail"]["a.webm"] = subprocess.TimeoutExpired("run", 5)
    res = go(env, timeout_s=5)
    assert res["a"]["status"] == "failed: timeout after 5 s" and res["b"]["status"] == "ok"
    assert not (env["runs"] / "a-p3e").exists()


def test_missing_video_failed(env: dict[str, Any]) -> None:
    (env["data"] / "a.webm").unlink()
    res = go(env)
    assert res["a"]["status"] == "failed: video missing (a.webm)"
    assert not (env["runs"] / "a-p3e").exists() and res["b"]["status"] == "ok"


def test_only_runs_in_given_order(env: dict[str, Any]) -> None:
    res = go(env, only=["b", "a"])
    assert list(res) == ["b", "a"] and list(env["calls"]) == ["b.webm", "a.webm"]
    with pytest.raises(StageError, match="unknown benchmark video"):
        go(env, only=["nope"])


def test_work_dir_inside_runs_refused(env: dict[str, Any]) -> None:
    before = snapshot(env["runs"])
    with pytest.raises(StageError, match="outside --runs"):
        refresh.refresh(env["data"], env["runs"], "p3e", manifest=env["manifest"],
                        work_dir=env["runs"] / "w")  # fmt: skip
    assert snapshot(env["runs"]) == before


def test_other_samples_listed_as_not_reference(env: dict[str, Any]) -> None:
    (env["data"] / "south-building/images").mkdir(parents=True)
    (env["data"] / "south-building.zip").write_bytes(b"zip")
    (env["data"] / ".DS_Store").write_bytes(b"")
    res = go(env)
    assert list(res) == ["a", "b", "south-building", "south-building.zip"]
    assert res["south-building"]["status"] == "skipped: not a reference video"


def test_dense_never_invoked(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """(e) the dense entry points raise if called; refresh still exports every video."""

    def boom(*a: Any, **k: Any) -> None:
        raise AssertionError("dense must not run")

    monkeypatch.setattr(dense_remote.RemoteDenseExecutor, "run", boom)
    monkeypatch.setattr(visibility, "load_dense", boom)
    res = go(env)
    assert all(r["status"] == "ok" for r in res.values())
    for n in ("a", "b"):
        m = json.loads((env["runs"] / f"{n}-p3e/export/web/manifest.json").read_text())
        assert "dense" not in m


def test_child_command_is_sparse_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The real run_video refresh uses: `qarau_pipeline run --video`, nothing dense."""
    seen: list[list[str]] = []

    def fake_subprocess_run(cmd: list[str], **kw: Any) -> None:
        seen.append(cmd)
        raise subprocess.TimeoutExpired(cmd, 1)

    monkeypatch.setattr(benchmark.subprocess, "run", fake_subprocess_run)
    with pytest.raises(subprocess.TimeoutExpired):
        refresh.run_video(tmp_path / "v.webm", tmp_path / "out", 1, 150)
    (cmd,) = seen
    assert cmd[1:5] == ["-m", "qarau_pipeline", "run", "--video"]
    assert not any("dense" in c for c in cmd)


def test_cli_refresh_report(env: dict[str, Any], tmp_path: Path) -> None:
    """The real benchmark_set.toml names; Markdown report outside runs; a failure -> exit 1."""
    for f in ("notre_dame_oubiels.webm", "IMG_8315.MOV"):
        (env["data"] / f).write_bytes(b"not a real video")
    env["fail"]["IMG_8315.MOV"] = StageError("boom")
    report = tmp_path / "report.md"
    with pytest.raises(SystemExit) as e:
        cli(env, "--only", "notre-dame,room", "--report", str(report))
    assert e.value.code == 1
    assert sorted(p.name for p in env["runs"].iterdir()) == ["notre-dame-p3e", *OLD]
    text = report.read_text()
    assert "| notre-dame | notre-dame-p3e | 75.0 | " in text
    assert "| room | room-p3e | — | — | — | — | — | failed: StageError: boom |" in text


def test_cli_report_inside_runs_refused(env: dict[str, Any]) -> None:
    before = snapshot(env["runs"])
    with pytest.raises(SystemExit) as e:
        cli(env, "--report", str(env["runs"] / "report.md"))
    assert e.value.code == 1 and env["calls"] == {}  # refused before any work
    assert snapshot(env["runs"]) == before
