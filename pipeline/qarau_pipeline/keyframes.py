"""Parallax keyframes: KLT tracks over the candidate frames; a new keyframe where the camera
moved enough to add 3D geometry, not on a fixed clock (docs/notes/p2b-plan.md)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from .interfaces import StageError

LONG_SIDE = 640  # tracking and quality work on a 640 px long-side grey image
MAX_CORNERS, MIN_DIST = 400, 12
MIN_EIG = 1e-3  # Shi-Tomasi score below this is sensor noise, not a corner (cornerMinEigenVal)
MIN_PAIR = 12  # common tracks needed to fit a homography
MODES = ("parallax", "fixed")

Tracks = tuple[np.ndarray, np.ndarray]  # track ids (N,), points (N, 2) in grey-image px


@dataclass(frozen=True)
class KeyframeParams:
    """Thresholds are fractions of the grey image diagonal (resolution independent).

    The frame budget (the old fixed-rate frame count) sets how many keyframes COLMAP gets:
    fewer frames lose points (the gate fails on -10 %). frames.run decodes `oversample` x
    that many candidates and `fit` raises the parallax threshold above `parallax_frac` until
    the keyframes fit the budget, so they go where the camera moves, not by the clock.
    parallax_frac itself is the floor: a still camera measures ~5e-5 (docs/notes/p2b-plan.md)."""

    mode: str = "parallax"
    parallax_frac: float = 0.0003  # median residual after the best homography -> new keyframe
    # candidates per budgeted keyframe; room at 2.0: frames bunch into the fast parts and
    # points fall 11 % (1.5: -2.6 %, within run-to-run spread; docs/notes/p2b-plan.md)
    oversample: float = 1.5
    min_track_frac: float = 0.6  # fewer of the keyframe's tracks left -> new keyframe
    rotation_min_gap_s: float = 0.3  # pure rotation: track-loss keyframes at most this often
    rotation_inliers: float = 0.9  # homography inlier ratio: "the homography explains it"
    rotation_max_parallax_frac: float = 0.002  # ... and no parallax left
    min_motion_frac: float = 0.01  # ... while the image did move

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise StageError(f"unknown keyframe mode {self.mode!r}; expected one of {MODES}")

    def label(self) -> str:
        if self.mode == "fixed":
            return "fixed"
        return (f"parallax >= {self.parallax_frac:g} fit to budget, {self.oversample:g}x "
                f"candidates, tracks {self.min_track_frac:g}, "
                f"rotation gap {self.rotation_min_gap_s:g} s")  # fmt: skip


@dataclass(frozen=True)
class Pair:
    common: int  # tracks seen in both frames
    survival: float  # share of frame a's tracks still alive in frame b
    flow: float  # median displacement, px
    parallax: float  # median residual after the RANSAC homography, px
    inliers: float  # homography inlier ratio


def grey(img: np.ndarray) -> np.ndarray:
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    s = LONG_SIDE / max(g.shape)
    return cv2.resize(g, None, fx=s, fy=s, interpolation=cv2.INTER_AREA) if s < 1 else g


def load_grey(path: Path) -> np.ndarray:
    g = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if g is None:
        raise StageError(f"cannot read extracted frame {path}")
    return grey(g)


def diagonal(g: np.ndarray) -> float:
    return float(np.hypot(*g.shape[:2]))


def _detect(g: np.ndarray, pts: np.ndarray, n: int) -> np.ndarray:
    mask = np.full(g.shape, 255, np.uint8)
    for x, y in pts.astype(int):
        cv2.circle(mask, (int(x), int(y)), MIN_DIST, 0, -1)
    new = cv2.goodFeaturesToTrack(g, n, 0.01, MIN_DIST, mask=mask)
    if new is None:
        return np.empty((0, 2), np.float32)
    new = new.reshape(-1, 2)
    eig = cv2.cornerMinEigenVal(g, 3)
    return new[eig[new[:, 1].astype(int), new[:, 0].astype(int)] >= MIN_EIG]


def _lk(a: np.ndarray, b: np.ndarray, pts: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    p = pts.reshape(-1, 1, 2).astype(np.float32)
    nxt, st, _ = cv2.calcOpticalFlowPyrLK(a, b, p, p.copy(), winSize=(21, 21), maxLevel=3)
    return np.asarray(nxt).reshape(-1, 2), np.asarray(st).ravel()


def track(greys: list[np.ndarray]) -> list[Tracks]:
    """Frame-to-frame pyramidal LK with a forward-backward check; new corners are added when
    fewer than 70 % of MAX_CORNERS are alive. Ids let any two frames be compared."""
    out: list[Tracks] = []
    ids, pts = np.empty(0, np.int64), np.empty((0, 2), np.float32)
    next_id, prev = 0, None
    for g in greys:
        if prev is not None and len(pts):
            nxt, st = _lk(prev, g, pts)
            back, st2 = _lk(g, prev, nxt)
            h, w = g.shape
            ok = (
                (st == 1) & (st2 == 1)
                & (np.linalg.norm(back - pts, axis=1) < 1.0)
                & (nxt[:, 0] >= 0) & (nxt[:, 0] < w) & (nxt[:, 1] >= 0) & (nxt[:, 1] < h)
            )  # fmt: skip
            ids, pts = ids[ok], nxt[ok]
        if len(pts) < 0.7 * MAX_CORNERS:
            new = _detect(g, pts, MAX_CORNERS - len(pts))
            ids = np.concatenate([ids, np.arange(next_id, next_id + len(new))])
            pts = np.concatenate([pts, new]).astype(np.float32)
            next_id += len(new)
        out.append((ids.copy(), pts.copy()))
        prev = g
    return out


def pair(a: Tracks, b: Tracks) -> Pair:
    _, ia, ib = np.intersect1d(a[0], b[0], assume_unique=True, return_indices=True)
    n = len(ia)
    survival = n / len(a[0]) if len(a[0]) else 1.0  # nothing to track: nothing lost
    if n < MIN_PAIR:
        return Pair(n, survival, float("nan"), float("nan"), 0.0)
    pa, pb = a[1][ia], b[1][ib]
    flow = float(np.median(np.linalg.norm(pb - pa, axis=1)))
    H, mask = cv2.findHomography(pa, pb, cv2.RANSAC, 2.0)
    if H is None:
        return Pair(n, survival, flow, flow, 0.0)
    proj = cv2.perspectiveTransform(pa.reshape(-1, 1, 2).astype(np.float64), H).reshape(-1, 2)
    res = float(np.median(np.linalg.norm(proj - pb, axis=1)))
    return Pair(n, survival, flow, res, float(mask.mean()))


def is_rotation(p: Pair, diag: float, inliers: float, max_par: float, min_motion: float) -> bool:
    """The homography explains the motion and the image moved, but nothing is left over for
    depth: turning on the spot (or a flat surface filmed head-on, which looks the same)."""
    return p.inliers >= inliers and p.parallax < max_par * diag and p.flow >= min_motion * diag


def subsample[T](keys: list[T], cap: int | None) -> list[T]:
    """Uniform by list index, first and last kept."""
    if not cap or len(keys) <= cap:
        return keys
    pick = np.unique(np.linspace(0, len(keys) - 1, max(cap, 2)).round().astype(int))
    return [keys[j] for j in pick]


def select(
    tracks: list[Tracks],
    times: list[float],
    usable: list[bool],
    diag: float,
    prm: KeyframeParams,
    cap: int | None = None,
) -> list[int]:
    """Indices of the keyframes among the usable candidates (see fit)."""
    return fit(tracks, times, usable, diag, prm, cap)[0]


def fit(
    tracks: list[Tracks],
    times: list[float],
    usable: list[bool],
    diag: float,
    prm: KeyframeParams,
    cap: int | None = None,
) -> tuple[list[int], float]:
    """(keyframes, parallax threshold used). With a cap, the threshold rises from
    prm.parallax_frac until the keyframes fit it (bisection); track-loss keyframes alone
    over the cap -> uniform subsample. Without a cap, prm.parallax_frac as is."""
    idx = [i for i, u in enumerate(usable) if u]
    if prm.mode == "fixed" or len(idx) <= 2:
        return subsample(idx, cap), 0.0
    cache: dict[tuple[int, int], Pair] = {}  # a pair does not depend on the threshold

    def walk(theta: float) -> list[int]:
        keys = [idx[0]]
        for i in idx[1:-1]:
            k = keys[-1]
            p = cache.get((k, i)) or cache.setdefault((k, i), pair(tracks[k], tracks[i]))
            if p.parallax >= theta * diag:
                keys.append(i)
            elif p.survival < prm.min_track_frac:
                rot = is_rotation(p, diag, prm.rotation_inliers, prm.rotation_max_parallax_frac,
                                  prm.min_motion_frac)  # fmt: skip
                if not rot or times[i] - times[k] >= prm.rotation_min_gap_s:
                    keys.append(i)
        return [*keys, idx[-1]]

    lo, keys = prm.parallax_frac, walk(prm.parallax_frac)
    if cap and len(keys) > cap:
        hi = 0.05  # 5 % of the diagonal: far above any 6-12 fps candidate step
        best = walk(hi)
        for _ in range(14):  # log bisection: smallest threshold whose keyframes fit
            mid = float(np.sqrt(lo * hi))
            got = walk(mid)
            if len(got) > cap:
                lo = mid
            else:
                hi, best = mid, got
        lo, keys = hi, best
    return subsample(keys, cap), lo


def params_dict(prm: KeyframeParams) -> dict[str, Any]:
    return {**asdict(prm), "label": prm.label()}
