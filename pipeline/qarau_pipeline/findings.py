"""Manual findings (DEF1-api, ADR 0022): an engineer marks a region of the map, this module
selects the bundle points inside it, measures it in bundle units and picks the evidence frames.

NumPy only, nothing heavy at import time: the API process imports this module per request.
Region in: a polygon in map coordinates (>= 3 xyz vertices) or a list of point indices.
Out: selected point indices, area (bundle units², hull of the points in their best-fit plane),
the best `N_EVIDENCE` frames from the bundle's point -> frame links with the region projected
into each frame as an outline polygon (pixels). Never a metre value: the scale is the API's job.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .cameras import project, rot_from_qvec

PLANE_TOL_FRAC = 0.05  # default distance to the polygon's plane: 5 % of its largest extent
N_EVIDENCE = 3


@dataclass
class Camera:
    """A registered frame: x_cam = R x + t (bundle world)."""

    R: np.ndarray
    t: np.ndarray
    model: str
    params: list[float]
    width: int
    height: int

    @property
    def center(self) -> np.ndarray:
        return np.asarray(-self.R.T @ self.t)


@dataclass
class Bundle:
    """The `points` layer of a viewer bundle (ADR 0003) with its point -> frame links as CSR:
    frames of point i = frame_ids[offsets[i]:offsets[i + 1]]."""

    positions: np.ndarray  # N x 3 float64
    offsets: np.ndarray  # N + 1 int64
    frame_ids: np.ndarray  # int64, indices into images / cameras
    cameras: dict[int, Camera]  # registered frames only
    images: list[str]  # per frame, image path relative to the run dir


def load_bundle(run_dir: Path) -> Bundle:
    man = json.loads((run_dir / "web/manifest.json").read_text())
    pos = np.fromfile(run_dir / man["points"]["position"], "<f4").reshape(-1, 3).astype(float)
    off = np.fromfile(run_dir / man["tracks"]["point_offsets"], "<u4").astype(np.int64)
    ids = np.fromfile(run_dir / man["tracks"]["point_frames"], "<u2").astype(np.int64)
    if len(off) != len(pos) + 1 or (len(off) and off[-1] != len(ids)):
        raise ValueError(f"{run_dir}: tracks CSR does not match {len(pos)} points")
    cams = {}
    for f in man["frames"]:
        if f.get("registered"):
            c = f["camera"]
            cams[int(f["i"])] = Camera(
                rot_from_qvec(np.asarray(f["pose"]["qvec"], float)),
                np.asarray(f["pose"]["tvec"], float),
                c["model"], [float(p) for p in c["params"]], int(c["width"]), int(c["height"]),
            )  # fmt: skip
    return Bundle(pos, off, ids, cams, [f["image"] for f in man["frames"]])


# ---------------------------------------------------------------- 2D helpers


def convex_hull(uv: np.ndarray) -> np.ndarray:
    """Andrew's monotone chain: hull vertices counter-clockwise (in a y-up frame) starting at
    the lowest (x, y). Fewer than 3 distinct or collinear points give the 1-2 extreme points."""
    pts = np.unique(np.asarray(uv, float).reshape(-1, 2), axis=0)  # sorted by x, then y
    if len(pts) < 3:
        return pts

    def half(seq: np.ndarray) -> list[np.ndarray]:
        h: list[np.ndarray] = []
        for p in seq:
            while len(h) >= 2:
                a, b = h[-1] - h[-2], p - h[-2]
                if a[0] * b[1] - a[1] * b[0] > 0:  # left turn: keep
                    break
                h.pop()
            h.append(p)
        return h

    lower, upper = half(pts), half(pts[::-1])
    return np.array(lower[:-1] + upper[:-1])


def polygon_area(uv: np.ndarray) -> float:
    """Shoelace, absolute."""
    if len(uv) < 3:
        return 0.0
    x, y = uv[:, 0], uv[:, 1]
    return float(abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))) / 2)


def points_in_polygon(uv: np.ndarray, poly: np.ndarray) -> np.ndarray:
    """Even-odd test of N points against one polygon (any shape, vertices in order)."""
    x, y = uv[:, 0], uv[:, 1]
    inside = np.zeros(len(uv), bool)
    with np.errstate(divide="ignore", invalid="ignore"):
        for (x0, y0), (x1, y1) in zip(poly, np.roll(poly, -1, axis=0), strict=True):
            crosses = (y0 > y) != (y1 > y)
            xi = x0 + (y - y0) * (x1 - x0) / (y1 - y0)
            inside ^= crosses & (x < xi)
    return inside


def clip_to_rect(poly: np.ndarray, width: float, height: float) -> np.ndarray:
    """Sutherland-Hodgman clip of a convex polygon to [0, width] x [0, height]."""
    out = [np.asarray(p, float) for p in poly]
    for axis, lim, keep_below in ((0, 0.0, False), (0, width, True), (1, 0.0, False),
                                  (1, height, True)):  # fmt: skip
        if not out:
            break
        inp, out = out, []
        prev = inp[-1]
        for cur in inp:
            cur_in = cur[axis] <= lim if keep_below else cur[axis] >= lim
            prev_in = prev[axis] <= lim if keep_below else prev[axis] >= lim
            if cur_in != prev_in:
                s = (lim - prev[axis]) / (cur[axis] - prev[axis])
                out.append(prev + s * (cur - prev))
            if cur_in:
                out.append(cur)
            prev = cur
    return np.array(out).reshape(-1, 2)


# ---------------------------------------------------------------- region -> points


def select_points(positions: np.ndarray, polygon: Any, tol: float | None = None) -> np.ndarray:
    """Indices (ascending) of the points inside the polygon: a plane is fitted through the
    vertices, points within `tol` of it (default PLANE_TOL_FRAC x the polygon's largest
    in-plane extent) are tested against the polygon in that plane. ValueError on a bad polygon."""
    poly = np.asarray(polygon, float)
    if poly.ndim != 2 or poly.shape[1] != 3 or len(poly) < 3 or not np.isfinite(poly).all():
        raise ValueError("polygon must be a list of at least 3 finite [x, y, z] vertices")
    c = poly.mean(0)
    _, s, vt = np.linalg.svd(poly - c, full_matrices=False)
    if s[1] <= 1e-9 * max(float(s[0]), 1e-12):
        raise ValueError("polygon vertices are collinear: no plane and no area")
    poly_uv = (poly - c) @ vt[:2].T
    if tol is None:
        tol = PLANE_TOL_FRAC * float(np.ptp(poly_uv, axis=0).max())
    d = np.asarray(positions, float) - c
    near = np.abs(d @ vt[2]) <= tol
    return np.flatnonzero(near & points_in_polygon(d @ vt[:2].T, poly_uv))


def select_indices(n_points: int, indices: Any) -> np.ndarray:
    """Validated, sorted point indices. ValueError when empty, non-integer, out of range or
    repeated."""
    idx = np.asarray(indices)
    if idx.ndim != 1 or len(idx) == 0 or idx.dtype.kind not in "iu":
        raise ValueError("point_indices must be a non-empty list of integers")
    if int(idx.min()) < 0 or int(idx.max()) >= n_points:
        raise ValueError(f"point index out of range 0..{n_points - 1}")
    if len(np.unique(idx)) != len(idx):
        raise ValueError("point_indices contains duplicates")
    return np.sort(idx.astype(np.int64))


# ---------------------------------------------------------------- area, frames, outlines


def region_area(points: np.ndarray) -> float:
    """Area of the convex hull of the points in their best-fit plane, in bundle units².
    Fewer than 3 points or collinear points: 0.0."""
    pts = np.asarray(points, float).reshape(-1, 3)
    if len(pts) < 3:
        return 0.0
    c = pts.mean(0)
    _, _, vt = np.linalg.svd(pts - c, full_matrices=False)
    return polygon_area(convex_hull((pts - c) @ vt[:2].T))


def rank_frames(selected: np.ndarray, bundle: Bundle, k: int = N_EVIDENCE) -> list[int]:
    """Up to k frame indices: first the frame that sees the most selected points, then
    greedily the frame with the best count x (1 - max cosine between its viewing direction
    to the region centroid and the directions of the frames already chosen). Ties: lower
    frame index. Frames that see no selected point are never returned."""
    sel = np.asarray(selected, np.int64)
    n_frames = len(bundle.images)
    starts, counts = bundle.offsets[sel], bundle.offsets[sel + 1] - bundle.offsets[sel]
    rows = np.repeat(starts - np.cumsum(counts) + counts, counts) + np.arange(int(counts.sum()))
    seen = np.bincount(bundle.frame_ids[rows], minlength=n_frames).astype(float)
    centroid = bundle.positions[sel].mean(0)
    dirs = np.zeros((n_frames, 3))
    for f, cam in bundle.cameras.items():
        v = cam.center - centroid
        dirs[f] = v / (np.linalg.norm(v) or 1.0)
    chosen: list[int] = []
    cand = seen > 0
    while len(chosen) < k and cand.any():
        score = seen * (1 - (dirs @ dirs[chosen].T).max(1)) if chosen else seen.copy()
        score[~cand] = -1.0
        f = int(np.argmax(score))  # first maximum = lowest frame index on a tie
        chosen.append(f)
        cand[f] = False
    return chosen


def project_outline(points: np.ndarray, cam: Camera) -> list[list[float]]:
    """The region as seen by `cam`: convex hull of the projections of the points in front of
    the camera, clipped to the image. [[u, v], ...] in pixels; [] when nothing is in view."""
    xc = np.asarray(points, float).reshape(-1, 3) @ cam.R.T + cam.t
    xc = xc[xc[:, 2] > 1e-9]
    if len(xc) < 3:
        return []
    # ponytail: strong radial distortion can fold points far outside the image back in;
    # mild (|k1| < 0.1) is fine for the hull. Undistort before the hull if that ever shows.
    hull = convex_hull(project(cam.model, cam.params, xc))
    if len(hull) < 3:
        return []
    clipped = clip_to_rect(hull, cam.width, cam.height)
    if len(clipped) < 3:
        return []
    return [[round(float(u), 2), round(float(v), 2)] for u, v in clipped]


def evidence_for(bundle: Bundle, selected: np.ndarray, k: int = N_EVIDENCE) -> list[dict[str, Any]]:
    """[{frame, image, outline}] for the k best frames of the selected points."""
    pts = bundle.positions[np.asarray(selected, np.int64)]
    return [
        {"frame": f, "image": bundle.images[f], "outline": project_outline(pts, bundle.cameras[f])}
        for f in rank_frames(selected, bundle, k)
    ]
