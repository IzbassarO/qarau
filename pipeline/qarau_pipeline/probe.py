"""ffprobe a video into a VideoInfo: codec, rotation, HDR, frame-rate regularity.

Parsing is split from running ffprobe so it is unit-testable on fixture dicts.
"""

from __future__ import annotations

import json
import re
import shutil
import statistics
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .interfaces import StageError

HDR_TRANSFERS = {"smpte2084", "arib-std-b67"}  # PQ, HLG (iPhone default)
VFR_PACKETS = 120  # pts deltas of the first N video packets decide is_vfr
# a frame interval more than 25 % off the median = variable frame rate. Timebase rounding is
# ~3 % at worst (33/34 ms in a 1 ms timebase); a dropped frame (2x) or a 10->30 fps switch is not.
VFR_TOLERANCE = 0.25


@dataclass(frozen=True)
class VideoInfo:
    codec: str
    width: int  # coded (as stored)
    height: int
    rotation_deg: int  # clockwise, 0/90/180/270; iPhone portrait = 90
    display_width: int  # after rotation
    display_height: int
    color_transfer: str
    color_primaries: str
    pix_fmt: str
    bit_depth: int
    is_hdr: bool
    avg_fps: float
    r_fps: float
    duration_s: float
    is_vfr: bool


def _rate(s: str | None) -> float:
    n, _, d = (s or "0/1").partition("/")
    return float(n) / float(d or 1) if float(d or 1) else 0.0


def rotation_deg(stream: dict[str, Any]) -> int:
    """Clockwise display rotation. ffprobe's displaymatrix `rotation` is counter-clockwise
    (iPhone portrait = -90); the legacy `tags.rotate` is clockwise (iPhone portrait = 90)."""
    for sd in stream.get("side_data_list", []):
        if "rotation" in sd:
            return round(-float(sd["rotation"]) / 90) * 90 % 360
    rot = stream.get("tags", {}).get("rotate")
    return round(float(rot) / 90) * 90 % 360 if rot else 0


def bit_depth(stream: dict[str, Any]) -> int:
    raw = stream.get("bits_per_raw_sample")
    if raw and str(raw).isdigit():
        return int(raw)
    m = re.search(r"(\d+)(le|be)$", stream.get("pix_fmt", ""))  # yuv420p10le, p010le
    return int(m.group(1)) if m else 8


def is_vfr(pts: list[float]) -> bool:
    """True when any frame interval deviates from the median by more than VFR_TOLERANCE."""
    ts = sorted(pts)  # packets come in decode order (B-frames)
    d = [b - a for a, b in zip(ts, ts[1:], strict=False)]
    if len(d) < 2:
        return False
    med = statistics.median(d)
    return med > 0 and max(abs(x - med) for x in d) > VFR_TOLERANCE * med


def parse(probe: dict[str, Any], pts: list[float]) -> VideoInfo:
    """probe = `ffprobe -show_streams -show_format -select_streams v:0` JSON; pts = packet times."""
    if not probe.get("streams"):
        raise StageError("no video stream in the file")
    s = probe["streams"][0]
    rot = rotation_deg(s)
    w, h = int(s["width"]), int(s["height"])
    trc, prim = s.get("color_transfer", ""), s.get("color_primaries", "")
    depth = bit_depth(s)
    dur = s.get("duration") or probe.get("format", {}).get("duration")
    if not dur or float(dur) <= 0:
        raise StageError("video has no duration (stream and container both lack it)")
    return VideoInfo(
        codec=s.get("codec_name", "unknown"), width=w, height=h, rotation_deg=rot,
        display_width=h if rot % 180 else w, display_height=w if rot % 180 else h,
        color_transfer=trc, color_primaries=prim, pix_fmt=s.get("pix_fmt", ""),
        bit_depth=depth, is_hdr=trc in HDR_TRANSFERS or (prim == "bt2020" and depth >= 10),
        avg_fps=_rate(s.get("avg_frame_rate")), r_fps=_rate(s.get("r_frame_rate")),
        duration_s=float(dur), is_vfr=is_vfr(pts),
    )  # fmt: skip


def _ffprobe(*args: str) -> dict[str, Any]:
    if shutil.which("ffprobe") is None:
        raise StageError("ffprobe not found on PATH: install FFmpeg (brew/apt install ffmpeg)")
    p = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", *args], capture_output=True, text=True
    )
    if p.returncode != 0:
        raise StageError(f"ffprobe: {p.stderr.strip()[-300:] or 'unknown error'}")
    out: dict[str, Any] = json.loads(p.stdout)
    return out


def probe_video(path: Path) -> VideoInfo:
    try:
        j = _ffprobe("-show_streams", "-show_format", "-select_streams", "v:0", str(path))
        pk = _ffprobe("-select_streams", "v:0", "-show_entries", "packet=pts_time",
                      "-read_intervals", f"%+#{VFR_PACKETS}", str(path))  # fmt: skip
        pts = [float(t) for x in pk.get("packets", []) if (t := x.get("pts_time", "N/A")) != "N/A"]
        return parse(j, pts)
    except StageError as e:
        raise StageError(f"cannot read video {path.name}: {e}") from e
