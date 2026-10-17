import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from qarau_pipeline import benchmark, reconstruction
from qarau_pipeline.cli import main
from qarau_pipeline.interfaces import StageError

FILES = [e["file"] for e in benchmark.load_manifest()]
OLD_TABLE = "# Benchmark\n\n| Video | Pipeline |\n| --- | --- |\n| room | M1 |\n"


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """data/ with every reference video (fake bytes), a fake repo, run_video mocked."""
    data, repo = tmp_path / "data", tmp_path / "repo"
    data.mkdir()
    for f in FILES:
        (data / f).write_bytes(b"not a real video")
    (repo / "docs/notes").mkdir(parents=True)
    (repo / "docs/notes/benchmark.md").write_text(OLD_TABLE)
    calls: dict[str, int | None] = {}
    fail: dict[str, BaseException] = {}

    def fake_run_video(
        video: Path, out: Path, timeout_s: float, max_frames: int | None = None
    ) -> dict[str, Any]:
        calls[video.name] = max_frames
        if video.name in fail:
            raise fail[video.name]
        out.mkdir(parents=True, exist_ok=True)
        (out / "log.txt").write_text("ok")
        return {"images_in": 120, "registered_images": 90, "models": 2, "points": 5000,
                "mean_reproj_error_px": 0.41,
                "dropped_points": 3 if video.name == "IMG_8315.MOV" else 0,
                "keyframes": "fixed" if video.name == "IMG_8315.MOV" else None}  # fmt: skip

    def fake_git(repo: Path, *args: str) -> str:
        return "abc1234" if args[0] == "rev-parse" else ""

    monkeypatch.setattr(benchmark, "run_video", fake_run_video)
    monkeypatch.setattr(benchmark, "_git", fake_git)
    return {"tmp": tmp_path, "data": data, "repo": repo, "out": tmp_path / "out",
            "calls": calls, "fail": fail}  # fmt: skip


def run(env: dict[str, Any], **kw: Any) -> tuple[int, dict[str, Any]]:
    code = benchmark.run_benchmark(env["data"], env["out"], repo_root=env["repo"], **kw)
    return code, json.loads((env["out"] / "results.json").read_text())


def snapshot(root: Path, skip: Path) -> dict[str, tuple[int, int]]:
    return {
        str(p): (p.stat().st_size, p.stat().st_mtime_ns)
        for p in root.rglob("*")
        if not p.is_relative_to(skip)
    }


def test_manifest() -> None:
    req = {e["file"]: e["required"] for e in benchmark.load_manifest()}
    assert req == {
        "aegviidu_water_tower.ogv": True,
        "mykolaiv_oil_tanks.webm": True,
        "notre_dame_oubiels.webm": True,
        "orono_water_tower.webm": True,
        "IMG_8315.MOV": False,
    }
    caps = {e["name"]: e.get("max_frames") for e in benchmark.load_manifest()}
    assert caps["aegviidu"] == caps["orono"] == 150 and caps["notre-dame"] is None


def test_results_schema(env: dict[str, Any]) -> None:
    code, res = run(env)
    assert code == 0
    top = {"commit", "dirty", "pipeline_version", "started_at", "videos", "errors", "skipped"}
    assert top <= set(res)
    assert res["commit"] == "abc1234" and res["dirty"] is False
    assert res["pipeline_version"] == "0.1.0"  # from pyproject: the project is not installed
    assert set(res["videos"]) == {"aegviidu", "mykolaiv", "notre-dame", "orono", "room"}
    # the manifest's frame cap reaches the pipeline and is recorded with the row
    assert env["calls"]["aegviidu_water_tower.ogv"] == 150
    assert env["calls"]["notre_dame_oubiels.webm"] is None
    assert res["videos"]["orono"]["max_frames"] == 150
    assert res["videos"]["room"]["max_frames"] is None
    for v in res["videos"].values():
        assert set(v) == {"registered_pct", "models", "reproj_px", "points", "scale_err_cm",
                          "time_s", "frames_used", "registered", "max_frames",
                          "dropped_points", "keyframes"}  # fmt: skip
        assert v["scale_err_cm"] is None
        assert (v["registered_pct"], v["frames_used"], v["registered"]) == (75.0, 120, 90)
        assert (v["models"], v["points"], v["reproj_px"]) == (2, 5000, 0.41)
    assert res["errors"] == {} and res["skipped"] == []
    md = (env["out"] / "results.md").read_text()
    assert md.count("| abc1234 |") == 5


class FakeModel:
    def __init__(self, reg: int, pts: int, first: int = 0) -> None:
        self.reg, self.pts, self.first = reg, pts, first

    def reg_image_ids(self) -> list[int]:
        return list(range(self.first, self.first + self.reg))

    def image(self, i: int) -> Any:
        return type("Image", (), {"name": f"{i:06d}.jpg"})

    def num_reg_images(self) -> int:
        return self.reg

    def num_points3D(self) -> int:
        return self.pts

    def compute_mean_track_length(self) -> float:
        return 3.0

    def compute_mean_reprojection_error(self) -> float:
        return 0.5 + self.reg / 1000

    def write_binary(self, path: str) -> None:
        (Path(path) / "points3D.bin").write_bytes(b"")


def test_largest_model_selected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pc = reconstruction.pycolmap
    monkeypatch.setattr(pc, "extract_features", lambda *a, **k: None)
    monkeypatch.setattr(pc, "match_exhaustive", lambda *a, **k: None)
    models = {0: FakeModel(10, 900, 80), 1: FakeModel(80, 4000), 2: FakeModel(30, 9000, 90)}
    monkeypatch.setattr(pc, "incremental_mapping", lambda *a, **k: models)
    names = [f"{i:06d}.jpg" for i in range(100)]
    rc = reconstruction.ColmapReconstructor(merge=False)
    m = rc.reconstruct(tmp_path, names, tmp_path / "colmap")
    row = benchmark.video_row(m, 12.34)
    assert (row["models"], row["registered"], row["points"]) == (3, 80, 4000)
    assert (row["frames_used"], row["registered_pct"], row["reproj_px"]) == (100, 80.0, 0.58)
    assert row["time_s"] == 12.3


def test_one_failure_others_still_run(env: dict[str, Any]) -> None:
    env["fail"]["mykolaiv_oil_tanks.webm"] = StageError("exit 1: COLMAP could not build a model")
    env["fail"]["orono_water_tower.webm"] = subprocess.TimeoutExpired("python", 5)
    code, res = run(env, timeout_s=5)
    assert code == 1
    assert res["errors"] == {
        "mykolaiv": "StageError: exit 1: COLMAP could not build a model",
        "orono": "timeout after 5 s",
    }
    assert set(res["videos"]) == {"aegviidu", "notre-dame", "room"}
    assert len(env["calls"]) == 5
    md = (env["out"] / "results.md").read_text()
    assert "| orono | abc1234 | — | — | — | — | — | — | — | FAILED: timeout after 5 s |" in md


def test_missing_required_fails(env: dict[str, Any]) -> None:
    (env["data"] / "orono_water_tower.webm").unlink()
    code, res = run(env)
    assert code == 1 and "orono" in res["errors"] and "orono" not in res["videos"]
    assert len(res["videos"]) == 4


def test_missing_optional_skipped(env: dict[str, Any]) -> None:
    (env["data"] / "IMG_8315.MOV").unlink()
    code, res = run(env)
    assert code == 0 and res["skipped"] == ["room"] and res["errors"] == {}
    assert len(res["videos"]) == 4


def test_no_writes_outside_out(env: dict[str, Any]) -> None:
    """--data unchanged and, without --record, nothing written outside --out."""
    before = snapshot(env["tmp"], env["out"])
    code, _ = run(env)
    assert code == 0 and snapshot(env["tmp"], env["out"]) == before
    assert {p.name for p in env["out"].iterdir()} >= {"results.json", "results.md", "notre-dame"}


def test_out_inside_data_refused(env: dict[str, Any]) -> None:
    with pytest.raises(StageError, match="inside --data"):
        benchmark.run_benchmark(env["data"], env["data"] / "out", repo_root=env["repo"])
    assert not (env["data"] / "out").exists()


def test_record_appends_rows(env: dict[str, Any]) -> None:
    run(env, record=True)
    code, res = run(env, record=True, only=["notre-dame"])
    assert code == 0
    saved = json.loads((env["repo"] / "docs/benchmarks/abc1234.json").read_text())
    assert saved == res
    md = (env["repo"] / "docs/notes/benchmark.md").read_text()
    assert md.startswith(OLD_TABLE)
    assert md.count(benchmark.SECTION) == 1
    assert md.count("| abc1234 |") == 6 and md.count("| notre-dame | abc1234 |") == 2
    assert (
        "| room | abc1234 | 120 | 75.0 | 2 | 5000 | 0.41 |  | 0.0 | dropped 3; keyframes fixed |"
        in md
    )
    assert "| notre-dame | abc1234 | 120 | 75.0 | 2 | 5000 | 0.41 |  | 0.0 |  |" in md
    assert "| aegviidu | abc1234 | 120 | 75.0 | 2 | 5000 | 0.41 |  | 0.0 | max_frames 150 |" in md


def test_cli(env: dict[str, Any]) -> None:
    argv = ["benchmark", "--data", str(env["data"]), "--out", str(env["out"])]
    with pytest.raises(SystemExit) as e:
        main([*argv, "--only", "notre-dame", "orono"])
    assert e.value.code == 0
    res = json.loads((env["out"] / "results.json").read_text())
    assert set(res["videos"]) == {"notre-dame", "orono"}
    with pytest.raises(SystemExit) as e:
        main([*argv, "--only", "nope"])
    assert e.value.code == 1
