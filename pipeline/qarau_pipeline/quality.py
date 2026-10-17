"""Footage quality: per candidate frame checks -> time ranges with plain re-shoot advice ->
quality_report.json (format: docs/notes/quality-report.md). Codes are stable: the web app
translates them."""

from __future__ import annotations

import bisect
from dataclasses import asdict, dataclass
from typing import Any

import cv2
import numpy as np

from . import keyframes as kf

SCHEMA_VERSION = 1
CODES = ("blur", "too_fast", "pure_rotation", "low_texture")
BELOW = {"laplacian_var", "parallax_frac"}  # metrics where a lower value is worse

ADVICE = {
    "blur": "Hold the camera steadier and move slower: frames at {when} are blurred.",
    "too_fast": "Move or turn slower: at {when} the view changes too much between frames "
    "for the map to follow.",
    "pure_rotation": "Walk along or around the object instead of turning on the spot: at "
    "{when} the camera only turns, which gives no depth.",
    "low_texture": "Keep the structure in view, not plain walls, sky or empty ground: at "
    "{when} most of the picture has no detail to measure.",
}

Flag = tuple[str, float, float]  # metric, value, threshold


@dataclass(frozen=True)
class QualityParams:
    """Fractions are of the 640 px grey image diagonal; times in seconds."""

    blur_min: float = 15.0  # Laplacian variance (frames.score): below = blurred ...
    blur_rel: float = 0.35  # ... or below this share of the video's median (drop rule)
    fast_flow_frac: float = 0.15  # median flow to the next candidate frame
    fast_lost_frac: float = 0.7  # share of tracks lost to the next candidate frame
    rotation_window_s: float = 1.0
    rotation_inliers: float = 0.9
    rotation_max_parallax_frac: float = 0.002
    rotation_min_motion_frac: float = 0.03
    texture_grid: tuple[int, int] = (8, 6)  # cells along the long, short side
    texture_empty_frac: float = 0.6  # share of cells without one Shi-Tomasi corner
    min_issue_s: float = 0.5
    major_frac: float = 0.15  # an issue this long (share of the video) needs a re-shoot


def empty_cells(g: np.ndarray, grid: tuple[int, int]) -> float:
    eig = cv2.cornerMinEigenVal(g, 3)
    h, w = eig.shape
    cols, rows = grid if w >= h else grid[::-1]
    cells = eig[: h // rows * rows, : w // cols * cols].reshape(rows, h // rows, cols, w // cols)
    return float((cells.max(axis=(1, 3)) < kf.MIN_EIG).mean())


def frame_flags(
    times: list[float],
    blur: list[float],
    greys: list[np.ndarray],
    tracks: list[kf.Tracks],
    prm: QualityParams,
) -> dict[str, list[Flag | None]]:
    n = len(times)
    diag = kf.diagonal(greys[0])
    flags: dict[str, list[Flag | None]] = {c: [None] * n for c in CODES}
    rel = prm.blur_rel * float(np.median(blur))
    thr = max(prm.blur_min, rel)
    for i in range(n):
        # blur also wipes out corners, and a plain surface has no edges to be sharp: a sudden
        # drop against the rest of the video is blur, else few corners is low texture
        e = empty_cells(greys[i], prm.texture_grid)
        if blur[i] < rel:
            flags["blur"][i] = ("laplacian_var", blur[i], thr)
        elif e >= prm.texture_empty_frac:
            flags["low_texture"][i] = ("empty_cells_frac", e, prm.texture_empty_frac)
        elif blur[i] < thr:
            flags["blur"][i] = ("laplacian_var", blur[i], thr)
    for i in range(1, n):
        if not len(tracks[i - 1][0]):
            continue  # nothing to track (flat image): low_texture says it
        p = kf.pair(tracks[i - 1], tracks[i])
        if p.flow >= prm.fast_flow_frac * diag:
            flags["too_fast"][i] = ("flow_frac", p.flow / diag, prm.fast_flow_frac)
        elif 1 - p.survival >= prm.fast_lost_frac:
            flags["too_fast"][i] = ("tracks_lost_frac", 1 - p.survival, prm.fast_lost_frac)
    for i in range(1, n):
        j = min(bisect.bisect_left(times, times[i] - prm.rotation_window_s), i - 1)
        p = kf.pair(tracks[j], tracks[i])
        if kf.is_rotation(p, diag, prm.rotation_inliers, prm.rotation_max_parallax_frac,
                          prm.rotation_min_motion_frac):  # fmt: skip
            for f in range(j, i + 1):
                flags["pure_rotation"][f] = ("parallax_frac", p.parallax / diag,
                                             prm.rotation_max_parallax_frac)  # fmt: skip
    return flags


def ranges(flagged: list[bool]) -> list[tuple[int, int]]:
    """Runs of flagged frames; a single unflagged frame does not split a run."""
    out: list[tuple[int, int]] = []
    for i, f in enumerate(flagged):
        if not f:
            continue
        if out and i - out[-1][1] <= 2:
            out[-1] = (out[-1][0], i)
        else:
            out.append((i, i))
    return out


def _when(a: float, b: float) -> str:
    return f"{a:.1f} s" if round(a, 1) == round(b, 1) else f"{a:.1f}–{b:.1f} s"


def issues(
    flags: dict[str, list[Flag | None]], times: list[float], duration: float, prm: QualityParams
) -> list[dict[str, Any]]:
    dt = float(np.median(np.diff(times))) if len(times) > 1 else 0.0
    out = []
    for code in CODES:
        fl = flags[code]
        for i0, i1 in ranges([f is not None for f in fl]):
            span = times[i1] - times[i0] + dt
            if span < prm.min_issue_s:
                continue
            got = [f for f in fl[i0 : i1 + 1] if f is not None]
            metric = max({f[0] for f in got}, key=[f[0] for f in got].count)
            vals = [f[1] for f in got if f[0] == metric]
            worst = min(vals) if metric in BELOW else max(vals)
            out.append({
                "code": code,
                "start_s": round(times[i0], 2),
                "end_s": round(times[i1], 2),
                "frames": [i0, i1],
                "severity": "major" if span >= prm.major_frac * duration else "minor",
                "metric": metric,
                "value": round(worst, 5),
                "threshold": round(next(f[2] for f in got if f[0] == metric), 5),
                "advice_en": ADVICE[code].format(when=_when(times[i0], times[i1])),
            })  # fmt: skip
    return sorted(out, key=lambda x: (x["start_s"], CODES.index(x["code"])))


def report(
    video: str,
    duration: float,
    times: list[float],
    blur: list[float],
    greys: list[np.ndarray],
    tracks: list[kf.Tracks],
    keyframes: int,
    selection: dict[str, Any],
    prm: QualityParams | None = None,
) -> dict[str, Any]:
    prm = prm or QualityParams()
    found = issues(frame_flags(times, blur, greys, tracks, prm), times, duration, prm)
    major = any(x["severity"] == "major" for x in found)
    return {
        "schema_version": SCHEMA_VERSION,
        "video": video,
        "duration_s": round(duration, 3),
        "candidate_frames": len(times),
        "keyframes": keyframes,
        "keyframe_selection": selection,
        "thresholds": asdict(prm),
        "verdict": "reshoot_recommended" if major else "ok",
        "issues": found,
    }
