import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest
import synth

from qarau_pipeline import frames, quality
from qarau_pipeline import keyframes as kf


def report(greys: list[np.ndarray]) -> dict[str, Any]:
    t = synth.times(len(greys))
    blur = [float(cv2.Laplacian(g, cv2.CV_64F).var()) for g in greys]
    sel = kf.params_dict(kf.KeyframeParams())
    rep = quality.report("synthetic.mp4", t[-1], t, blur, greys, kf.track(greys), 0, sel)
    check_shape(rep)
    return rep


def codes(rep: dict[str, Any]) -> set[str]:
    return {i["code"] for i in rep["issues"]}


def check_shape(rep: dict[str, Any]) -> None:
    assert rep["schema_version"] == 1 and rep["verdict"] in ("ok", "reshoot_recommended")
    assert {"video", "duration_s", "candidate_frames", "keyframes", "keyframe_selection",
            "thresholds", "issues"} <= rep.keys()  # fmt: skip
    for i in rep["issues"]:
        assert i["code"] in quality.CODES and i["severity"] in ("minor", "major")
        assert 0 <= i["start_s"] <= i["end_s"] <= rep["duration_s"] + 1e-6
        assert isinstance(i["value"], float) and isinstance(i["threshold"], float)
        assert isinstance(i["metric"], str) and i["advice_en"].endswith(".")
        assert " s" in i["advice_en"]  # names the time range
    json.dumps(rep)


def test_clean_translation_has_no_issues() -> None:
    rep = report(synth.translate(30))
    assert rep["issues"] == [] and rep["verdict"] == "ok"


def test_blur() -> None:
    greys = synth.translate(30)
    greys[12:24] = [cv2.GaussianBlur(g, (0, 0), 3) for g in greys[12:24]]
    rep = report(greys)
    [b] = [i for i in rep["issues"] if i["code"] == "blur"]
    assert b["frames"] == [12, 23] and (b["start_s"], b["end_s"]) == (2.0, 3.83)
    assert b["metric"] == "laplacian_var" and b["value"] < b["threshold"]
    assert b["severity"] == "major" and rep["verdict"] == "reshoot_recommended"
    assert "blurred" in b["advice_en"] and "2.0–3.8 s" in b["advice_en"]


def test_too_fast() -> None:
    tex = synth.texture()
    greys = [tex[:480, 140 * k : 140 * k + 640] for k in range(12)]  # 140 px per frame
    [f] = [i for i in rep_issues(greys) if i["code"] == "too_fast"]
    assert f["frames"][0] == 1 and f["frames"][1] == 11
    assert f["value"] >= f["threshold"] and "slower" in f["advice_en"]


def rep_issues(greys: list[np.ndarray]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = report(greys)["issues"]
    return issues


def test_pure_rotation() -> None:
    rep = report(synth.rotate(30))
    assert codes(rep) == {"pure_rotation"}
    [r] = rep["issues"]
    assert r["frames"] == [0, 29] and r["metric"] == "parallax_frac"
    assert r["value"] < r["threshold"] and "turning on the spot" in r["advice_en"]


def test_low_texture() -> None:
    rng = np.random.default_rng(1)
    greys = [np.clip(128 + rng.normal(0, 0.5, (480, 640)), 0, 255).astype(np.uint8)] * 12
    rep = report(greys)
    assert codes(rep) == {"low_texture"}  # a plain surface is not reported as blurred
    [t] = rep["issues"]
    assert t["value"] == 1.0 and t["metric"] == "empty_cells_frac"


def test_ranges() -> None:
    assert quality.ranges([False, True, True, False, True, False, False, True]) == [(1, 4), (7, 7)]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_frames_stage_writes_report(tmp_path: Path) -> None:
    v = tmp_path / "t.mp4"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
         "testsrc2=duration=3:size=320x240:rate=30", "-pix_fmt", "yuv420p", str(v)],
        check=True,
    )  # fmt: skip
    m = frames.run(v, tmp_path / "out")
    rep = json.loads((tmp_path / "out/quality_report.json").read_text())
    check_shape(rep)
    # budget 60 (3 s), 1.5x candidates = all 90 source frames (30 fps)
    assert rep["video"] == "t.mp4" and rep["candidate_frames"] == m["frames_extracted"] == 90
    assert rep["keyframes"] == m["keyframes"] <= rep["keyframe_selection"]["budget"] == 60
    assert rep["keyframe_selection"]["mode"] == "parallax"
    assert m["keyframe_mode"].startswith("parallax") and m["quality_verdict"] == rep["verdict"]
    fj = json.loads((tmp_path / "out/frames.json").read_text())["frames"]
    assert sum(f["kept"] for f in fj) == m["keyframes"] >= 2
    assert all(f["drop_reason"] in (None, "blur", "exposure", "not_keyframe") for f in fj)
    fixed = frames.run(v, tmp_path / "fixed", keys=kf.KeyframeParams(mode="fixed"))
    assert fixed["keyframes"] == 60 - fixed["frames_dropped"] and fixed["keyframe_mode"] == "fixed"
