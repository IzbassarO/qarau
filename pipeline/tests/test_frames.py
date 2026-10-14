import json
import shutil
import subprocess
from pathlib import Path

import cv2
import numpy as np
import pytest

from qarau_pipeline import frames
from qarau_pipeline.interfaces import StageError, read_run, run_stage

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


@pytest.fixture(scope="module")
def video(tmp_path_factory: pytest.TempPathFactory) -> Path:
    p = tmp_path_factory.mktemp("v") / "test.mp4"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
         "testsrc=duration=3:size=640x360:rate=25", "-pix_fmt", "yuv420p", str(p)],
        check=True,
    )  # fmt: skip
    return p


def test_frames_stage(video: Path, tmp_path: Path) -> None:
    m = frames.run(video, tmp_path)
    fj = json.loads((tmp_path / "frames.json").read_text())
    fr = fj["frames"]
    assert m["frames_extracted"] == len(fr) == 60  # clamp(3 s * 2.5, 60, 300)
    assert [f["i"] for f in fr] == list(range(60))
    ts = [f["t"] for f in fr]
    assert ts == sorted(ts) and 0 <= ts[0] < 0.1 and 2.8 < ts[-1] <= 3.0
    assert all((tmp_path / f["file"]).exists() for f in fr)
    assert all(f["blur"] > 0 and 0 < f["exposure"]["mean"] < 255 for f in fr)
    assert fj["source"]["width"] == 640 and abs(fj["source"]["duration_s"] - 3) < 0.1
    assert sum(not f["kept"] for f in fr) <= 0.3 * 60


def test_max_frames_caps(video: Path, tmp_path: Path) -> None:
    assert frames.run(video, tmp_path, max_frames=20)["frames_extracted"] == 20
    assert frames.target_frames(1000, 120) == 120 and frames.target_frames(10, None) == 60


def test_blurred_scores_lower(tmp_path: Path) -> None:
    img = (np.random.default_rng(0).random((480, 640)) * 255).astype(np.uint8)
    cv2.imwrite(str(tmp_path / "s.jpg"), img)
    cv2.imwrite(str(tmp_path / "b.jpg"), cv2.GaussianBlur(img, (0, 0), 4))
    assert frames.score(tmp_path / "b.jpg")[0] < 0.1 * frames.score(tmp_path / "s.jpg")[0]


def test_drop_rule() -> None:
    blur = [100.0] * 10 + [5.0]
    expo = [{"mean": 120.0, "clip_low": 0.0, "clip_high": 0.0}] * 11
    assert frames.decide_drops(blur, expo)[-1] == "blur"
    expo[0] = {"mean": 10.0, "clip_low": 0.0, "clip_high": 0.0}
    assert frames.decide_drops(blur, expo)[0] == "exposure"
    # everything bad: at most 30 % dropped
    bad = frames.decide_drops([1.0, 2.0, 3.0, 4.0, 5.0, 1000.0] * 5, [expo[0]] * 30)
    assert sum(r is not None for r in bad) == 9


def test_stage_failure_is_recorded(tmp_path: Path) -> None:
    def boom() -> dict:
        raise StageError("nope")

    with pytest.raises(StageError):
        run_stage(tmp_path, "frames", "t", {}, boom)
    r = read_run(tmp_path)
    assert r[0]["status"] == "failed" and "nope" in r[0]["error"]
    run_stage(tmp_path, "frames", "t", {}, lambda: {"ok": 1})  # re-run replaces entry
    assert len(read_run(tmp_path)) == 1 and read_run(tmp_path)[0]["status"] == "ok"
