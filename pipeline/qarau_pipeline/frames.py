"""frames stage: video -> JPEG frames at an adaptive rate + blur/exposure scores."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from .interfaces import StageError

# 6 fps: hand-held phone pans lose overlap at 2.5 fps (room test: 14 -> 257 registered)
MIN_FRAMES, MAX_FRAMES, FRAMES_PER_S = 60, 300, 6.0
BLUR_RATIO, MAX_DROP = 0.35, 0.30
# sky legitimately saturates ~40-45 % of drone frames; only near-white frames are exposure failures
CLIP_HIGH_MAX = 0.7
LONG_SIDE = 1920
HDR_TRANSFERS = {"arib-std-b67", "smpte2084"}  # HLG (iPhone default), PQ
# ponytail: `colorspace` reads HLG/PQ as BT.2020 gamma (no zscale in Homebrew ffmpeg); colours
# are right, HDR highlights clip. Swap for zscale+tonemap once every build we ship has libzimg.
HDR_TO_SDR = "colorspace=all=bt709:iall=bt2020:itrc=bt2020-10:format=yuv420p"


def probe(video: Path) -> dict[str, Any]:
    p = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height,avg_frame_rate,color_transfer,color_primaries:"
         "stream_side_data=rotation:format=duration", "-of", "json", str(video)],
        capture_output=True, text=True,
    )  # fmt: skip
    if p.returncode != 0:
        raise StageError(f"ffprobe failed: {p.stderr.strip()[:300]}")
    j = json.loads(p.stdout)
    s = j["streams"][0]
    n, d = (int(x) for x in s["avg_frame_rate"].split("/"))
    rot = next((int(x["rotation"]) for x in s.get("side_data_list", []) if "rotation" in x), 0)
    w, h = (s["height"], s["width"]) if rot % 180 else (s["width"], s["height"])
    trc = s.get("color_transfer", "")
    return {
        "width": w, "height": h,  # as displayed: ffmpeg auto-rotates frames
        "rotation": rot, "hdr": trc in HDR_TRANSFERS or s.get("color_primaries") == "bt2020",
        "color_transfer": trc,
        "fps": n / d if d else 0.0, "duration_s": float(j["format"]["duration"]),
    }  # fmt: skip


def target_frames(duration: float, max_frames: int | None = None) -> int:
    """clamp(duration * 6, 60, 300); an explicit max_frames replaces the 300 cap."""
    return int(min(max_frames or MAX_FRAMES, max(MIN_FRAMES, duration * FRAMES_PER_S)))


def score(path: Path) -> tuple[float, dict[str, float]]:
    g = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if g is None:
        raise StageError(f"cannot read extracted frame {path}")
    h, w = g.shape
    if w > 640:
        g = cv2.resize(g, (640, round(h * 640 / w)), interpolation=cv2.INTER_AREA)
    blur = float(cv2.Laplacian(g, cv2.CV_64F).var())
    return blur, {
        "mean": float(g.mean()),
        "clip_low": float((g < 10).mean()),
        "clip_high": float((g > 245).mean()),
    }


def decide_drops(blur: list[float], expo: list[dict[str, float]]) -> list[str | None]:
    """Adaptive rule; never drops more than MAX_DROP of the frames (worst blur dropped first)."""
    med = float(np.median(blur))
    reasons: list[str | None] = []
    for b, e in zip(blur, expo, strict=True):
        if (
            e["mean"] < 20
            or e["mean"] > 235
            or e["clip_low"] > 0.4
            or e["clip_high"] > CLIP_HIGH_MAX
        ):
            reasons.append("exposure")
        elif b < BLUR_RATIO * med:
            reasons.append("blur")
        else:
            reasons.append(None)
    cand = sorted((i for i, r in enumerate(reasons) if r), key=lambda i: blur[i])
    # ponytail: cap keeps the lowest-blur candidates dropped; ordering by blur only
    for i in cand[int(MAX_DROP * len(blur)) :]:
        reasons[i] = None
    return reasons


def run(video: Path, out: Path, max_frames: int | None = None) -> dict[str, Any]:
    info = probe(video)
    out.mkdir(parents=True, exist_ok=True)
    fdir = out / "frames"
    fdir.mkdir(exist_ok=True)
    for old in fdir.glob("*.jpg"):
        old.unlink()
    n_target = target_frames(info["duration_s"], max_frames)
    rate = min(n_target / info["duration_s"], info["fps"] or 1e9)
    vf = f"fps={rate:.6f},"
    if info["hdr"]:
        vf += HDR_TO_SDR + ","
    vf += (f"scale='min({LONG_SIDE},iw)':'min({LONG_SIDE},ih)':force_original_aspect_ratio=decrease"
           ":force_divisible_by=2,showinfo")  # fmt: skip
    p = subprocess.run(
        ["ffmpeg", "-hide_banner", "-y", "-i", str(video), "-an", "-vf", vf, "-q:v", "2",
         "-start_number", "0", str(fdir / "%06d.jpg")],
        capture_output=True, text=True,
    )  # fmt: skip
    if p.returncode != 0:
        raise StageError(f"ffmpeg failed: {p.stderr.strip()[-300:]}")
    times = [float(x) for x in re.findall(r"pts_time:\s*([0-9.]+)", p.stderr)]
    files = sorted(fdir.glob("*.jpg"))
    if not files:
        raise StageError("ffmpeg produced no frames")
    if len(times) != len(files):
        raise StageError(f"timecode mismatch: {len(times)} showinfo lines, {len(files)} files")
    scores = [score(f) for f in files]
    blur, expo = [s[0] for s in scores], [s[1] for s in scores]
    reasons = decide_drops(blur, expo)
    frames = [
        {"i": i, "file": f"frames/{f.name}", "t": round(times[i], 4), "blur": round(blur[i], 2),
         "exposure": expo[i], "kept": reasons[i] is None, "drop_reason": reasons[i]}
        for i, f in enumerate(files)
    ]  # fmt: skip
    source = {"kind": "video", "name": video.name, **info}
    (out / "frames.json").write_text(json.dumps({"source": source, "frames": frames}, indent=1))
    dropped = sum(1 for r in reasons if r)
    return {
        "frames_extracted": len(files), "frames_dropped": dropped, "rate_fps": round(rate, 4),
        "median_blur": round(float(np.median(blur)), 2),
    }  # fmt: skip


def from_images(images: Path, out: Path) -> dict[str, Any]:
    """Image-set input: link images as frames/000000.jpg.., t = index; write frames.json."""
    from .export import frames_from_images

    fr = frames_from_images(images, out)
    if not fr:
        raise StageError(f"no images found in {images}")
    first = cv2.imread(str(out / fr[0]["file"]))
    if first is None:
        raise StageError(f"cannot read {fr[0]['file']}")
    h, w = first.shape[:2]
    source = {"kind": "images", "name": images.name, "duration_s": float(len(fr) - 1),
              "fps": 1.0, "width": w, "height": h}  # fmt: skip
    frames = [{k: f[k] for k in ("i", "file", "t", "blur", "kept", "drop_reason")} for f in fr]
    (out / "frames.json").write_text(json.dumps({"source": source, "frames": frames}, indent=1))
    return {"frames_extracted": len(fr), "frames_dropped": 0}
