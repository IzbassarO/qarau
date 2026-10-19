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
    """Two fake videos, a runs dir with one old run, run_video = real export of a synthetic
    wall model (so the bundle and its created_at come from export.py)."""
    data, runs = tmp_path / "data", tmp_path / "runs"
    data.mkdir()
    for f in ("a.webm", "b.webm"):
        (data / f).write_bytes(b"not a real video")
    (runs / "old/web").mkdir(parents=True)
    (runs / "old/web/manifest.json").write_text('{"run_id": "old"}')
    (runs / "old/cloud.ply").write_bytes(b"\x00\x01ply")
    (runs / "old-link").symlink_to("old")
    manifest = tmp_path / "set.toml"
    manifest.write_text(SET)
    s = synth.wall_scene()
    sparse, images = tmp_path / "sparse", tmp_path / "images"
    synth.write_wall_model(s, sparse)
    images.mkdir()
    for i in s["views"]:
        cv2.imwrite(str(images / f"f{i}.jpg"), np.zeros((240, 320, 3), np.uint8))
    calls: dict[str, int | None] = {}
    fail: dict[str, Exception] = {}

    def fake_run_video(
        video: Path, out: Path, timeout_s: float, max_frames: int | None = None
    ) -> dict[str, Any]:
        calls[video.name] = max_frames
        (out / "web").mkdir(parents=True, exist_ok=True)
        (out / "web/partial.f32").write_bytes(b"half")  # a run that dies mid-way
        if video.name in fail:
            raise fail[video.name]
        m = {"images_in": 8, "registered_images": len(s["views"])}
        run_stage(out, "reconstruction", "fake", {}, lambda: m)
        main(["export", "--sparse", str(sparse), "--images", str(images), "--out", str(out)])
        return m

    monkeypatch.setattr(refresh, "run_video", fake_run_video)
    return {"data": data, "runs": runs, "manifest": manifest, "calls": calls, "fail": fail}


def go(env: dict[str, Any], **kw: Any) -> dict[str, dict[str, Any]]:
    return refresh.refresh(env["data"], env["runs"], "2026-10-18", manifest=env["manifest"], **kw)


def cli(env: dict[str, Any], *extra: str) -> None:
    main(["refresh-runs", "--data", str(env["data"]), "--runs", str(env["runs"]),
          "--suffix", "2026-10-18", *extra])  # fmt: skip


def snapshot(root: Path) -> dict[str, Any]:
    return {
        str(p.relative_to(root)): (
            str(p.readlink()) if p.is_symlink() else p.is_dir() or p.read_bytes(),
            p.lstat().st_mtime_ns,
        )
        for p in root.rglob("*")
    }


def test_new_runs_next_to_old_ones(env: dict[str, Any]) -> None:
    before = snapshot(env["runs"])
    res = go(env)
    assert sorted(p.name for p in env["runs"].iterdir()) == [
        "a-2026-10-18", "b-2026-10-18", "old", "old-link"
    ]  # fmt: skip
    after = snapshot(env["runs"])
    assert {k: after[k] for k in before} == before  # old run byte-identical, mtimes too
    assert env["calls"] == {"a.webm": None, "b.webm": 150}  # per-video max_frames reused
    r = res["b"]
    assert r["status"] == "ok" and r["run"] == "b-2026-10-18"
    assert r["keyframes"] == 8 and r["registered_pct"] == 75.0
    m = json.loads((env["runs"] / "b-2026-10-18/web/manifest.json").read_text())
    assert m["run_id"] == "b-2026-10-18"
    assert r["points"] == m["stats"]["points"] > 0 and r["far_points"] == m["stats"]["far_points"]
    assert "| b | b-2026-10-18 | 75.0 |" in refresh.markdown(res)


def test_created_at_is_utc_iso(env: dict[str, Any]) -> None:
    t0 = datetime.now(UTC).replace(microsecond=0)
    go(env, only=["a"])
    m = json.loads((env["runs"] / "a-2026-10-18/web/manifest.json").read_text())
    created = datetime.fromisoformat(m["created_at"])
    assert created.utcoffset() == timedelta(0) and created >= t0  # taken at export, not copied


def test_existing_run_skipped_byte_identical(env: dict[str, Any]) -> None:
    (env["runs"] / "b-2026-10-18/web").mkdir(parents=True)
    (env["runs"] / "b-2026-10-18/web/manifest.json").write_text('{"keep": "me"}')
    (env["runs"] / "a-2026-10-18").symlink_to("missing-target")  # dangling link counts too
    before = snapshot(env["runs"])
    res = go(env)
    assert snapshot(env["runs"]) == before and env["calls"] == {}  # nothing run, nothing written
    for n in ("a", "b"):
        assert res[n] == {**dict.fromkeys(refresh.COLS), "run": f"{n}-2026-10-18",
                          "status": "skipped: run already exists"}  # fmt: skip


def test_failure_reported_and_next_video_exported(env: dict[str, Any]) -> None:
    env["fail"]["a.webm"] = StageError("exit 1: no registered images")
    res = go(env)
    assert res["a"] == {**dict.fromkeys(refresh.COLS), "run": "a-2026-10-18",
                        "status": "failed: StageError: exit 1: no registered images"}  # fmt: skip
    assert res["b"]["status"] == "ok" and res["b"]["points"] > 0
    names = sorted(p.name for p in env["runs"].iterdir())
    assert names == ["b-2026-10-18", "old", "old-link"]  # no partial a, no temp dir left


def test_timeout_reported(env: dict[str, Any]) -> None:
    env["fail"]["a.webm"] = subprocess.TimeoutExpired("run", 5)
    res = go(env, timeout_s=5)
    assert res["a"]["status"] == "failed: timeout after 5 s" and res["b"]["status"] == "ok"
    assert not (env["runs"] / "a-2026-10-18").exists()


def test_missing_video_skipped(env: dict[str, Any]) -> None:
    (env["data"] / "a.webm").unlink()
    res = go(env)
    assert res["a"]["status"].startswith("skipped: video missing")
    assert not (env["runs"] / "a-2026-10-18").exists() and res["b"]["status"] == "ok"


def test_other_samples_listed_as_not_reference(env: dict[str, Any]) -> None:
    (env["data"] / "south-building/images").mkdir(parents=True)
    (env["data"] / "south-building.zip").write_bytes(b"zip")
    (env["data"] / ".DS_Store").write_bytes(b"")
    res = go(env)
    assert list(res) == ["a", "b", "south-building", "south-building.zip"]
    assert res["south-building"]["status"] == "skipped: not a reference video"
    assert "south-building" not in go(env, only=["a"])  # --only: just the named videos


def test_dense_never_invoked(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a: Any, **k: Any) -> None:
        raise AssertionError("dense must not run")

    monkeypatch.setattr(dense_remote.RemoteDenseExecutor, "run", boom)
    monkeypatch.setattr(visibility, "load_dense", boom)
    res = go(env)
    assert all(r["status"] == "ok" for r in res.values())
    for n in ("a", "b"):
        assert "dense" not in json.loads(
            (env["runs"] / f"{n}-2026-10-18/web/manifest.json").read_text()
        )


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


def test_cli_refresh_runs(env: dict[str, Any]) -> None:
    """The real benchmark_set.toml names; a failed video makes the command exit 1."""
    for f in ("notre_dame_oubiels.webm", "IMG_8315.MOV"):
        (env["data"] / f).write_bytes(b"not a real video")
    env["fail"]["IMG_8315.MOV"] = StageError("boom")
    with pytest.raises(SystemExit) as e:
        cli(env, "--only", "notre-dame,room")
    assert e.value.code == 1
    assert (env["runs"] / "notre-dame-2026-10-18/web/manifest.json").is_file()
    assert not (env["runs"] / "room-2026-10-18").exists()
    summary = json.loads((env["runs"] / "refresh-2026-10-18.json").read_text())
    assert all(set(r) == {*refresh.COLS} for r in summary.values())  # every column, every row
    assert summary["notre-dame"]["status"] == "ok" and summary["notre-dame"]["keyframes"] == 8
    assert summary["room"]["status"] == "failed: StageError: boom"
    assert "| room | room-2026-10-18 | — |" in refresh.markdown(summary)


def test_cli_never_overwrites_summary(env: dict[str, Any], tmp_path: Path) -> None:
    (env["runs"] / "refresh-2026-10-18.json").write_text("old")
    before = snapshot(env["runs"])
    with pytest.raises(SystemExit):
        cli(env)
    assert snapshot(env["runs"]) == before and env["calls"] == {}  # refused before any work
    with pytest.raises(SystemExit) as e:
        cli(env, "--out", str(tmp_path / "elsewhere"))
    assert e.value.code == 0 and (tmp_path / "elsewhere/refresh-2026-10-18.json").is_file()
