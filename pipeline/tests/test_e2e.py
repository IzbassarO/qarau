import json
from pathlib import Path

import pytest

from qarau_pipeline.cli import main

VIDEO = (
    Path(__file__).resolve().parents[2].parent.parent.parent
    / "data/samples/notre_dame_oubiels.webm"
)
# worktree lives in <repo>/.claude/worktrees/<name>; fall back to the env-free common location
CANDIDATES = [VIDEO, Path(__file__).resolve().parents[2] / "data/samples/notre_dame_oubiels.webm"]


@pytest.mark.slow
def test_church_video_end_to_end(tmp_path: Path) -> None:
    video = next((p for p in CANDIDATES if p.exists()), None)
    if video is None:
        pytest.skip("sample video not present")
    main(["run", "--video", str(video), "--out", str(tmp_path)])
    m = json.loads((tmp_path / "web/manifest.json").read_text())
    assert m["stats"]["frames_registered"] > 20 and m["stats"]["points"] > 1000
    assert (tmp_path / "cloud.laz").exists()
    assert {s["name"] for s in json.loads((tmp_path / "run.json").read_text())} == {
        "frames",
        "reconstruction",
        "export",
    }
