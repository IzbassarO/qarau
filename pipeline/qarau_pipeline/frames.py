"""frames stage: video -> JPEG candidate frames at an adaptive rate + blur/exposure scores ->
parallax keyframes (keyframes.py) + footage quality report (quality.py).

Decoding is driven by probe.VideoInfo: ffmpeg autorotates, HDR is tone-mapped to 8-bit
BT.709, frames are picked by presentation time. frames/manifest.json records what was done.
"""

from __future__ import annotations

import functools
import json
import re
import shutil
import subprocess
from dataclasses import asdict
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from . import keyframes as kf
from . import quality
from .interfaces import StageError
from .probe import VideoInfo, probe_video

# 6 fps: hand-held phone pans lose overlap at 2.5 fps (room test: 14 -> 257 registered)
MIN_FRAMES, MAX_FRAMES, FRAMES_PER_S = 60, 300, 6.0
BLUR_RATIO, MAX_DROP = 0.35, 0.30
# sky legitimately saturates ~40-45 % of drone frames; only near-white frames are exposure failures
CLIP_HIGH_MAX = 0.7
LONG_SIDE = 1920
# zscale (libzimg) linearises HLG/PQ so `tonemap` can compress the highlights into BT.709 SDR
HDR_HABLE = (
    "zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
    "tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p"
)
# ponytail: fallback for builds without libzimg (Homebrew ffmpeg): `colorspace` reads HLG/PQ as
# BT.2020 gamma, so colours are right but HDR highlights clip. Warned in the manifest.
HDR_COLORSPACE = "colorspace=all=bt709:iall=bt2020:itrc=bt2020-10:format=yuv420p"
NO_ZSCALE = ("ffmpeg has no zscale filter (libzimg): HDR converted with colorspace "
             "BT.2020->BT.709 without tone mapping, bright highlights may clip")  # fmt: skip


@functools.cache
def has_zscale() -> bool:
    p = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True)
    return any(line.split()[1:2] == ["zscale"] for line in p.stdout.splitlines())


def pts_select(rate: float, frame_dt: float) -> str:
    """Same frames as the old `fps=rate` filter (round=near: per output tick k the last source
    frame with t < (k+0.5)/rate), but by presentation time without duplicates, so the source
    pts survive. select cannot look ahead, so "last frame before B" is taken as "first frame at
    or after B - frame_dt", frame_dt = median frame interval (1/r_fps is off for ms-rounded
    pts: 33.37 vs 33 ms). Exact for CFR; with ms-rounded
    pts (webm) a frame exactly at a boundary may go one frame either way; within one frame
    interval for VFR. Slot k of frame t = floor((t + frame_dt)*rate - 0.5)."""

    def slot(t: str) -> str:
        return f"floor(({t}+{frame_dt:.6f})*{rate:.6f}-0.5)"

    return (f"select='if(isnan(prev_selected_t),gte({slot('t')},0),"
            f"gt({slot('t')},{slot('prev_selected_t')}))'")  # fmt: skip


def decode_plan(info: VideoInfo, rate: float, zscale: bool) -> tuple[str, list[str], list[str]]:
    """ffmpeg -vf chain, conversions applied, warnings."""
    conv = [f"autorotate:{info.rotation_deg}"] if info.rotation_deg else []  # ffmpeg default
    warn: list[str] = []
    fps = info.r_fps or info.avg_fps
    vf = [pts_select(rate, info.frame_dt_s or (1 / fps if fps else 0.0))]
    if info.is_vfr:
        conv.append("vfr:pts_sampling")
    if info.is_hdr:
        vf.append(HDR_HABLE if zscale else HDR_COLORSPACE)
        conv.append("tonemap:hable" if zscale else "tonemap:colorspace")
        warn += [] if zscale else [NO_ZSCALE]
    vf.append(f"scale='min({LONG_SIDE},iw)':'min({LONG_SIDE},ih)':force_original_aspect_ratio="
              "decrease:force_divisible_by=2,showinfo")  # fmt: skip
    return ",".join(vf), conv, warn


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


def run(
    video: Path,
    out: Path,
    max_frames: int | None = None,
    keys: kf.KeyframeParams | None = None,
) -> dict[str, Any]:
    """Candidates on the fixed-rate grid -> quality drops -> keyframes among the usable ones
    (keys.mode 'parallax' by default, 'fixed' = every usable candidate) -> frames.json and
    quality_report.json. Candidates that are not keyframes: kept=False, 'not_keyframe'."""
    keys = keys or kf.KeyframeParams()
    if shutil.which("ffmpeg") is None:
        raise StageError("ffmpeg not found on PATH: install FFmpeg (brew/apt install ffmpeg)")
    info = probe_video(video)
    out.mkdir(parents=True, exist_ok=True)
    fdir = out / "frames"
    fdir.mkdir(exist_ok=True)
    for old in [*fdir.glob("*.jpg"), fdir / "manifest.json"]:
        old.unlink(missing_ok=True)
    n_target = target_frames(info.duration_s, max_frames)
    rate = min(n_target / info.duration_s, info.avg_fps or 1e9)
    vf, conv, warn = decode_plan(info, rate, has_zscale())
    p = subprocess.run(
        ["ffmpeg", "-hide_banner", "-y", "-i", str(video), "-map", "0:v:0", "-an", "-vf", vf,
         "-fps_mode", "passthrough", "-q:v", "2", "-start_number", "0", str(fdir / "%06d.jpg")],
        capture_output=True, text=True,
    )  # fmt: skip
    tail = p.stderr.strip()[-300:]
    if p.returncode != 0:
        raise StageError(f"ffmpeg could not decode {video.name} ({info.codec}): {tail}")
    times = [float(x) for x in re.findall(r"pts_time:\s*(-?[0-9.]+)", p.stderr)]
    files = sorted(fdir.glob("*.jpg"))
    if not files:
        raise StageError(
            f"ffmpeg produced 0 frames from {video.name} ({info.codec}, "
            f"{info.duration_s:.1f} s); truncated or unsupported file? {tail}"
        )
    if len(times) != len(files):
        raise StageError(f"timecode mismatch: {len(times)} showinfo lines, {len(files)} files")
    # rotation must happen exactly once (ffmpeg JPEGs carry no EXIF orientation for OpenCV)
    first = cv2.imread(str(files[0]))
    if first is None:
        raise StageError(f"cannot read extracted frame {files[0]}")
    fh, fw = first.shape[:2]
    if abs(fw / fh - info.display_width / info.display_height) > 0.02:
        raise StageError(
            f"frames are {fw}x{fh} but {video.name} displays as "
            f"{info.display_width}x{info.display_height}: rotation applied wrongly"
        )
    pts = [{"file": f.name, "pts_s": round(t, 6)} for f, t in zip(files, times, strict=True)]
    manifest = {"source": asdict(info), "conversions": conv, "frames": pts, "warnings": warn}
    (fdir / "manifest.json").write_text(json.dumps(manifest, indent=1))
    scores = [score(f) for f in files]
    blur, expo = [s[0] for s in scores], [s[1] for s in scores]
    reasons = decide_drops(blur, expo)
    dropped = sum(1 for r in reasons if r)
    greys = [kf.load_grey(f) for f in files]
    tracks = kf.track(greys)
    usable = [r is None for r in reasons]
    chosen = set(kf.select(tracks, times, usable, kf.diagonal(greys[0]), keys, n_target))
    reasons = [r or (None if i in chosen else "not_keyframe") for i, r in enumerate(reasons)]
    selection = kf.params_dict(keys)
    rep = quality.report(video.name, info.duration_s, times, blur, greys, tracks, len(chosen),
                         selection)  # fmt: skip
    (out / "quality_report.json").write_text(json.dumps(rep, indent=1))
    frames = [
        {"i": i, "file": f"frames/{f.name}", "t": round(times[i], 4), "blur": round(blur[i], 2),
         "exposure": expo[i], "kept": reasons[i] is None, "drop_reason": reasons[i]}
        for i, f in enumerate(files)
    ]  # fmt: skip
    source = {
        "kind": "video",
        "name": video.name,
        "width": info.display_width,
        "height": info.display_height,
        "fps": info.avg_fps,
        "duration_s": info.duration_s,
        "codec": info.codec,
        "rotation_deg": info.rotation_deg,
        "is_hdr": info.is_hdr,
    }
    (out / "frames.json").write_text(json.dumps({"source": source, "frames": frames}, indent=1))
    return {
        "frames_extracted": len(files), "frames_dropped": dropped, "keyframes": len(chosen),
        "keyframe_mode": keys.label(), "keyframe_selection": selection,
        "quality_verdict": rep["verdict"], "quality_issues": [i["code"] for i in rep["issues"]],
        "rate_fps": round(rate, 4), "median_blur": round(float(np.median(blur)), 2),
        "conversions": conv, "warnings": warn,
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
