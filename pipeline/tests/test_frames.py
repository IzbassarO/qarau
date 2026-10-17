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
from qarau_pipeline.interfaces import StageError, read_run, run_stage
from qarau_pipeline.keyframes import KeyframeParams
from qarau_pipeline.probe import parse, probe_video

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
FIXED = KeyframeParams(mode="fixed")


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
    # budget clamp(3 s * 6, 60, 300) = 60 keyframes; 1.5x candidates, capped at the 25 fps source
    assert m["frames_extracted"] == len(fr) == 75
    assert [f["i"] for f in fr] == list(range(75))
    ts = [f["t"] for f in fr]
    assert ts == sorted(ts) and 0 <= ts[0] < 0.1 and 2.8 < ts[-1] <= 3.0
    assert all((tmp_path / f["file"]).exists() for f in fr)
    assert all(f["blur"] > 0 and 0 < f["exposure"]["mean"] < 255 for f in fr)
    assert fj["source"]["width"] == 640 and abs(fj["source"]["duration_s"] - 3) < 0.1
    assert sum(f["drop_reason"] in ("blur", "exposure") for f in fr) <= 0.3 * 75
    assert sum(f["kept"] for f in fr) == m["keyframes"] <= 60
    m = frames.run(video, tmp_path, keys=FIXED)
    assert m["frames_extracted"] == 60 and m["keyframes"] == 60 - m["frames_dropped"]


def test_max_frames_caps(video: Path, tmp_path: Path) -> None:
    # 20 < 3 s * 6 (60 by the minimum): a capped budget is sparse already, every frame stays
    m = frames.run(video, tmp_path, max_frames=20)
    assert m["frames_extracted"] == 20 and m["keyframes"] == 20 - m["frames_dropped"]
    assert m["keyframe_mode"].endswith("-> fixed") and m["keyframe_selection"]["budget_capped"]
    m = frames.run(video, tmp_path, max_frames=100)  # cap above the budget: parallax
    assert m["frames_extracted"] == 75 and 2 <= m["keyframes"] <= 60
    assert not m["keyframe_selection"]["budget_capped"]
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


def ffmpeg(*args: str) -> None:
    p = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args],
                       capture_output=True, text=True)  # fmt: skip
    assert p.returncode == 0, p.stderr


def clip(path: Path, *args: str, src: str = "testsrc2=duration=3:size=320x240:rate=30") -> Path:
    ffmpeg("-f", "lavfi", "-i", src, *args, str(path))
    return path


def need_x265() -> None:
    enc = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True)
    if "libx265" not in enc.stdout:
        pytest.skip("this ffmpeg build has no libx265 encoder (needed to make HEVC clips)")


def extract(video: Path, out: Path, max_frames: int | None = None) -> dict:
    frames.run(video, out, max_frames, FIXED)  # decoding tests: the plain fixed-rate grid
    m = json.loads((out / "frames/manifest.json").read_text())
    assert {"source", "conversions", "frames", "warnings"} <= m.keys()
    assert all({"file", "pts_s"} <= f.keys() for f in m["frames"])
    return m


def first_frame(out: Path, m: dict) -> np.ndarray:
    img = cv2.imread(str(out / "frames" / m["frames"][0]["file"]), cv2.IMREAD_UNCHANGED)
    assert img is not None
    return img


def test_h264_control(tmp_path: Path) -> None:
    v = clip(tmp_path / "a.mp4", "-c:v", "libx264", "-profile:v", "baseline", "-pix_fmt", "yuv420p")
    m = extract(v, tmp_path / "out")
    assert m["source"]["codec"] == "h264" and m["conversions"] == [] and m["warnings"] == []
    assert len(m["frames"]) == 60 and m["frames"][0]["file"] == "000000.jpg"
    assert first_frame(tmp_path / "out", m).shape == (240, 320, 3)


def test_hevc(tmp_path: Path) -> None:
    need_x265()
    v = clip(tmp_path / "b.mp4", "-c:v", "libx265", "-x265-params", "log-level=error",
             "-pix_fmt", "yuv420p", "-tag:v", "hvc1")  # fmt: skip
    m = extract(v, tmp_path / "out")
    assert m["source"]["codec"] == "hevc" and m["conversions"] == []
    assert len(m["frames"]) == 60 and first_frame(tmp_path / "out", m).shape == (240, 320, 3)


def test_hdr_pq_to_sdr(tmp_path: Path) -> None:
    need_x265()
    pq = "colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc"
    v = clip(tmp_path / "c.mp4", "-c:v", "libx265", "-x265-params", f"log-level=error:{pq}",
             "-pix_fmt", "yuv420p10le", "-color_primaries", "bt2020", "-color_trc", "smpte2084",
             "-colorspace", "bt2020nc", "-tag:v", "hvc1")  # fmt: skip
    m = extract(v, tmp_path / "out")
    src = m["source"]
    assert src["is_hdr"] and src["bit_depth"] == 10 and src["color_transfer"] == "smpte2084"
    # with zscale (CI's apt ffmpeg) the hable chain must run end to end, no silent fallback
    if frames.has_zscale():
        assert m["conversions"] == ["tonemap:hable"] and m["warnings"] == []
    else:
        assert m["conversions"] == ["tonemap:colorspace"] and "zscale" in m["warnings"][0]
    img = first_frame(tmp_path / "out", m)
    assert img.dtype == np.uint8 and img.shape == (240, 320, 3)  # 8-bit SDR JPEG
    sat = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)[..., 1]
    assert sat.mean() > 40  # testsrc2 is colourful; a grey/washed-out conversion is not


def test_hable_chain_runs() -> None:
    """The zscale+hable chain itself, on a PQ-tagged 10-bit lavfi source (no libx265 needed)."""
    if not frames.has_zscale():
        pytest.skip("this ffmpeg build has no zscale filter (libzimg); hable chain not runnable")
    tag = "setparams=color_primaries=bt2020:color_trc=smpte2084:colorspace=bt2020nc"
    vf = f"format=yuv420p10le,{tag},{frames.HDR_HABLE},showinfo"
    p = subprocess.run(
        ["ffmpeg", "-hide_banner", "-f", "lavfi", "-i", "testsrc2=d=0.2:s=320x240:r=10",
         "-vf", vf, "-f", "null", "-"],
        capture_output=True, text=True,
    )  # fmt: skip
    assert p.returncode == 0, p.stderr[-800:]
    assert "fmt:yuv420p " in p.stderr  # 8-bit out


def test_rotation_90(tmp_path: Path) -> None:
    v0 = clip(tmp_path / "base.mp4", "-c:v", "libx264", "-pix_fmt", "yuv420p")
    v = tmp_path / "d.mp4"
    try:  # ffmpeg >= 6: display matrix; older builds: legacy rotate tag
        ffmpeg("-display_rotation", "90", "-i", str(v0), "-c", "copy", str(v))
    except AssertionError:
        ffmpeg("-i", str(v0), "-c", "copy", "-metadata:s:v:0", "rotate=90", str(v))
    m = extract(v, tmp_path / "out")
    assert m["source"]["rotation_deg"] in (90, 270)
    assert (m["source"]["display_width"], m["source"]["display_height"]) == (240, 320)
    assert m["conversions"] == [f"autorotate:{m['source']['rotation_deg']}"]
    # 240 wide x 320 high, rotated exactly once
    assert all(first_frame(tmp_path / "out", {"frames": [f]}).shape == (320, 240, 3)
               for f in m["frames"][::10])  # fmt: skip


def test_vfr_sampled_by_pts(tmp_path: Path) -> None:
    src = "testsrc2=s=320x240:r=10:d=1.5[a];testsrc2=s=320x240:r=30:d=1.5[b];[a][b]concat=n=2:v=1"
    v = clip(tmp_path / "e.mp4", "-fps_mode", "passthrough", "-c:v", "libx264",
             "-pix_fmt", "yuv420p", src=src)  # fmt: skip
    m = extract(v, tmp_path / "out", max_frames=18)  # 3 s -> 6 fps target
    assert m["source"]["is_vfr"] and "vfr:pts_sampling" in m["conversions"]
    pts = [f["pts_s"] for f in m["frames"]]
    source_pts = [i / 10 for i in range(15)] + [1.5 + i / 30 for i in range(45)]
    assert all(min(abs(p - s) for s in source_pts) < 2e-3 for p in pts)  # real source frames
    d = np.diff(pts)
    assert (d > 0).all()  # monotonic
    # first source frame in each 1/6 s slot: off the grid by at most one source interval (0.1 s)
    assert (np.abs(d - 1 / 6) <= 0.1 + 1e-3).all() and abs(d.mean() - 1 / 6) < 0.02
    assert 16 <= len(pts) <= 18


def test_decode_plan() -> None:
    s = {"codec_name": "hevc", "width": 3840, "height": 2160, "pix_fmt": "yuv420p10le",
         "color_transfer": "arib-std-b67", "duration": "3", "tags": {"rotate": "90"}}  # fmt: skip
    info = parse({"streams": [s]}, [i / 30 for i in range(60)])
    vf, conv, warn = frames.decode_plan(info, 6.0, zscale=True)
    assert conv == ["autorotate:90", "tonemap:hable"] and not warn
    assert vf.startswith("select=") and "tonemap=hable" in vf and vf.endswith("showinfo")
    vf, conv, warn = frames.decode_plan(info, 6.0, zscale=False)
    assert conv[-1] == "tonemap:colorspace" and "colorspace=" in vf and "zscale" in warn[0]


def test_unreadable_video_fails_clearly(tmp_path: Path) -> None:
    bad = tmp_path / "bad.mov"
    bad.write_bytes(b"\0" * 4096)
    with pytest.raises(StageError, match="cannot read video bad.mov"):
        frames.run(bad, tmp_path / "out")
    assert not list((tmp_path / "out").glob("frames/*"))


def test_zero_frames_fails_clearly(tmp_path: Path) -> None:
    v = clip(
        tmp_path / "full.mp4", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart"
    )
    cut = tmp_path / "cut.mp4"
    data = v.read_bytes()
    cut.write_bytes(data[: data.index(b"mdat") + 64])  # header intact, no frame data
    with pytest.raises(StageError, match="0 frames|could not decode"):
        frames.run(cut, tmp_path / "out")


def test_portrait_hdr_video(tmp_path: Path) -> None:
    """iPhone-style input: rotated, 10-bit HLG BT.2020 -> upright SDR frames, long side 1920."""
    need_x265()
    v0, v = tmp_path / "hlg.mov", tmp_path / "phone.mov"
    hlg = "colorprim=bt2020:transfer=arib-std-b67:colormatrix=bt2020nc"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
         "testsrc=duration=1:size=2560x1440:rate=30", "-c:v", "libx265", "-x265-params",
         f"log-level=error:{hlg}", "-pix_fmt", "yuv420p10le", "-color_primaries", "bt2020",
         "-color_trc", "arib-std-b67", "-colorspace", "bt2020nc", str(v0)],
        check=True,
    )  # fmt: skip
    # rotation as a container tag (like an iPhone), pixels stay landscape
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-display_rotation", "90", "-i", str(v0),
         "-c", "copy", str(v)],
        check=True,
    )  # fmt: skip
    info = probe_video(v)
    assert info.is_hdr and info.rotation_deg in (90, 270)
    assert (info.display_width, info.display_height) == (1440, 2560)
    frames.run(v, tmp_path / "out", max_frames=60)
    img = cv2.imread(str(tmp_path / "out/frames/000000.jpg"))
    assert img is not None
    assert img.shape[:2] == (1920, 1080)  # upright portrait, long side capped


# Parallax keyframes (keyframes.py) on synthetic sequences (tests/synth.py, 6 fps)


def select(greys: list[np.ndarray], cap: int | None = None, **kw: object) -> list[int]:
    prm = kf.KeyframeParams(**kw)  # type: ignore[arg-type]
    n = len(greys)
    return kf.select(kf.track(greys), synth.times(n), [True] * n, kf.diagonal(greys[0]), prm, cap)


def test_static_camera_at_most_two_keyframes() -> None:
    assert select(synth.static(30)) == [0, 29]
    # 10 s still: five max_gap_s spans, but near-duplicates never become keyframes
    assert select(synth.static(60)) == [0, 59]


def test_translation_gives_several_keyframes() -> None:
    assert len(select(synth.translate(40))) >= 10
    assert len(select(synth.translate(40), cap=10)) == 10  # fit to the budget


def test_translation_spaced_by_threshold() -> None:
    greys = synth.translate(40)
    fine, coarse = select(greys, parallax_frac=0.005), select(greys, parallax_frac=0.01)
    gap_f, gap_c = np.diff(fine[:-1]), np.diff(coarse[:-1])
    # ~0.8 px of parallax per frame against a 4 px / 8 px threshold (diagonal 800 px)
    assert set(gap_f) <= {4, 5, 6, 7} and set(gap_c) <= {8, 9, 10, 11, 12}
    assert 1.6 < np.mean(gap_c) / np.mean(gap_f) < 2.5
    assert len(select(greys, mode="fixed")) == 40


def test_max_time_gap() -> None:
    # parallax threshold out of reach (40 px), tracks survive: only the 2 s gap adds frames
    assert select(synth.translate(40), parallax_frac=0.05) == [0, 12, 24, 36, 39]


def test_rotation_only_gives_few_keyframes() -> None:
    greys = synth.rotate(40)  # 30 deg turn: no parallax, tracks leave the view
    keys = select(greys)
    assert keys[0] == 0 and keys[-1] == 39 and len(keys) <= 5
    # a moving camera over a scene with depth, same defaults: many more
    assert len(select(synth.translate(40))) > 3 * len(keys)


def test_max_frames_cap() -> None:
    greys = synth.translate(40)
    assert len(select(greys, parallax_frac=0.005)) > 6
    prm = kf.KeyframeParams(parallax_frac=0.005)
    keys, theta = kf.fit(kf.track(greys), synth.times(40), [True] * 40, 800.0, prm, cap=6)
    # the threshold rises until the keyframes fit, still evenly spaced by parallax
    assert 4 <= len(keys) <= 6 and keys[0] == 0 and keys[-1] == 39 and theta > 0.005
    assert select(greys, cap=6, mode="fixed") == [0, 8, 16, 23, 31, 39]
    # track-loss keyframes alone above the cap: uniform subsample
    assert len(select(synth.translate(25, (30, 60, 30, 60, 30, 60)), cap=5)) == 5


def test_budget_goes_where_the_camera_moves() -> None:
    greys = synth.static(20) + synth.translate(40)[1:21]  # still for 20 frames, then moving
    keys = select(greys, cap=10)
    assert len(keys) <= 10 and keys[0] == 0 and keys[-1] == 39
    assert sum(k >= 20 for k in keys) >= len(keys) - 2  # still part: first frame (+1 at most)
    assert len(select(greys, cap=10, mode="fixed")) == 10  # by the clock: half wasted


def test_unusable_frames_never_keyframes() -> None:
    greys = synth.translate(20)
    usable = [i not in (0, 5, 19) for i in range(20)]
    keys = kf.select(kf.track(greys), synth.times(20), usable, 800.0, kf.KeyframeParams())
    assert keys[0] == 1 and keys[-1] == 18 and 5 not in keys


def test_unknown_mode() -> None:
    with pytest.raises(StageError, match="keyframe mode"):
        kf.KeyframeParams(mode="every-third")


# Footage quality report (quality.py), format: docs/notes/quality-report.md


def report(greys: list[np.ndarray]) -> dict[str, Any]:
    t = synth.times(len(greys))
    blur = [float(cv2.Laplacian(g, cv2.CV_64F).var()) for g in greys]
    sel = kf.params_dict(kf.KeyframeParams())
    rep = quality.report("synthetic.mp4", t[-1], t, blur, greys, kf.track(greys), 2, sel)
    check_shape(rep)
    return rep


def issues(greys: list[np.ndarray], kind: str) -> list[dict[str, Any]]:
    return [i for i in report(greys)["issues"] if i["kind"] == kind]


def check_shape(rep: dict[str, Any]) -> None:
    """The documented fields, with their types and ranges."""
    assert rep["version"] == quality.VERSION == 2
    assert {"video", "duration_s", "summary", "keyframe_selection", "thresholds",
            "issues"} <= rep.keys()  # fmt: skip
    s = rep["summary"]
    assert set(s) == {"frames_total", "keyframes", "issues_count", "verdict"}
    assert s["issues_count"] == len(rep["issues"]) and 0 <= s["keyframes"] <= s["frames_total"]
    assert s["verdict"] == ("reshoot_recommended" if any(
        i["severity"] == "warn" for i in rep["issues"]) else "ok")  # fmt: skip
    for i in rep["issues"]:
        assert {"kind", "start_s", "end_s", "frames", "severity", "metric", "value",
                "threshold", "advice", "advice_key"} == i.keys()  # fmt: skip
        assert i["kind"] in quality.KINDS and i["severity"] in ("info", "warn")
        assert 0 <= i["start_s"] <= i["end_s"] <= rep["duration_s"] + 1e-6
        assert i["frames"] == sorted(i["frames"]) and 0 <= i["frames"][0] < s["frames_total"]
        assert isinstance(i["value"], float) and isinstance(i["threshold"], float)
        assert i["advice"].endswith(".") and " s" in i["advice"]  # names the time range
        assert i["advice_key"] == f"quality.advice.{i['kind']}"
    json.dumps(rep)


def test_clean_translation_has_no_issues() -> None:
    rep = report(synth.translate(30))
    assert rep["issues"] == [] and rep["summary"]["verdict"] == "ok"


def test_blur() -> None:
    greys = synth.translate(30)
    greys[12:24] = [cv2.GaussianBlur(g, (0, 0), 3) for g in greys[12:24]]
    rep = report(greys)
    [b] = [i for i in rep["issues"] if i["kind"] == "blur"]
    assert b["frames"] == list(range(12, 24)) and (b["start_s"], b["end_s"]) == (2.0, 3.83)
    assert b["metric"] == "laplacian_var" and b["value"] < b["threshold"]
    assert b["severity"] == "warn" and rep["summary"]["verdict"] == "reshoot_recommended"
    assert "blurred" in b["advice"] and "2.0–3.8 s" in b["advice"]
    assert "[warn] blur: Hold the camera steadier" in quality.text(rep)


def test_too_fast() -> None:
    tex = synth.texture()
    greys = [tex[:480, 140 * k : 140 * k + 640] for k in range(12)]  # 140 px per frame
    [f] = issues(greys, "too_fast")
    assert f["frames"][0] == 1 and f["frames"][-1] == 11
    assert f["value"] >= f["threshold"] and "slower" in f["advice"]


def test_pure_rotation() -> None:
    rep = report(synth.rotate(30))  # K R K^-1 warps: a homography explains all motion
    assert {i["kind"] for i in rep["issues"]} == {"pure_rotation"}
    [r] = rep["issues"]
    assert r["frames"] == list(range(30)) and r["metric"] == "parallax_frac"
    assert r["value"] < r["threshold"] and "turning on the spot" in r["advice"]


def test_low_texture() -> None:
    rng = np.random.default_rng(1)
    greys = [np.clip(128 + rng.normal(0, 0.5, (480, 640)), 0, 255).astype(np.uint8)] * 12
    rep = report(greys)
    assert {i["kind"] for i in rep["issues"]} == {"low_texture"}  # plain is not blurred
    [t] = rep["issues"]
    assert t["value"] == 1.0 and t["metric"] == "empty_cells_frac"


def test_ranges() -> None:
    assert quality.ranges([False, True, True, False, True, False, False, True]) == [(1, 4), (7, 7)]


def test_frames_stage_writes_report(tmp_path: Path) -> None:
    v = clip(tmp_path / "t.mp4", "-pix_fmt", "yuv420p")
    m = frames.run(v, tmp_path / "out")
    rep = json.loads((tmp_path / "out/quality_report.json").read_text())
    check_shape(rep)
    s = rep["summary"]
    # budget 60 (3 s), 1.5x candidates = all 90 source frames (30 fps)
    assert rep["video"] == "t.mp4" and s["frames_total"] == m["frames_extracted"] == 90
    assert s["keyframes"] == m["keyframes"] <= rep["keyframe_selection"]["budget"] == 60
    assert rep["keyframe_selection"]["mode"] == "parallax"
    assert m["keyframe_mode"].startswith("parallax") and m["quality_verdict"] == s["verdict"]
    fj = json.loads((tmp_path / "out/frames.json").read_text())["frames"]
    assert sum(f["kept"] for f in fj) == m["keyframes"] >= 2
    assert all(f["drop_reason"] in (None, "blur", "exposure", "not_keyframe") for f in fj)
    fixed = frames.run(v, tmp_path / "fixed", keys=FIXED)
    assert fixed["keyframes"] == 60 - fixed["frames_dropped"] and fixed["keyframe_mode"] == "fixed"
