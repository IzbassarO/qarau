"""scale: metric scale from marked points (an A4 sheet or one known distance) and gravity from
the cameras refined by the floor (ADR 0018). Reference file: docs/notes/scale-reference-format.md.

Everything here builds one similarity x' = s * A @ x + T from COLMAP world to the bundle world
(Z up, floor at z = 0, metres when a reference is given). Export applies it to points and
poses; it is always computed from the untouched COLMAP model, so a rerun never compounds."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pycolmap

from .interfaces import StageError

KINDS = {"distance": 2, "a4": 4}  # kind -> number of marked points
A4_SHORT, A4_LONG = 0.210, 0.297  # m, ISO 216
A4_DIAG = math.hypot(A4_SHORT, A4_LONG)  # 0.3638 m
MAX_TILT_DEG = 30.0  # floor normal vs the camera estimate of up
PLANE_TOL = 0.01  # RANSAC inlier distance, fraction of the cloud's 2-98 % extent
# a floor needs this share of the (sampled) points: a slab 2 x PLANE_TOL thick through a
# structureless blob holds ~3-6 %; the room / notre-dame floors hold 34 % / 53 %
PLANE_MIN_FRAC = 0.15
PLANE_MIN_POINTS = 50
RANSAC_ITERS = 500
RANSAC_SAMPLE = 20000  # points scored per hypothesis; the refit uses all of them
UP_AXIS = "+z"


# ---------------------------------------------------------------- reference file


@dataclass(frozen=True)
class Observation:
    frame: str  # frame file name as in frames.json, e.g. 000123.jpg
    x: float  # pixels in the extracted frame; (0, 0) = top-left corner of the image
    y: float


@dataclass(frozen=True)
class RefPoint:
    id: str
    observations: list[Observation]


@dataclass(frozen=True)
class ScaleRef:
    kind: str  # "distance" | "a4"
    points: list[RefPoint]
    distance_m: float | None = None


def _num(v: Any, what: str) -> float:
    if isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(v):
        raise StageError(f"scale reference: {what} must be a finite number, got {v!r}")
    return float(v)


def parse_reference(doc: Any) -> ScaleRef:
    """Validate a version-1 reference document; every problem is a StageError naming the field."""
    if not isinstance(doc, dict):
        raise StageError("scale reference: expected a JSON object")
    if doc.get("version") != 1:
        raise StageError(f"scale reference: unsupported version {doc.get('version')!r}, expected 1")
    kind = doc.get("kind")
    if kind not in KINDS:
        raise StageError(f"scale reference: kind must be 'distance' or 'a4', got {kind!r}")
    pts = doc.get("points")
    if not isinstance(pts, list) or len(pts) != KINDS[kind]:
        n = len(pts) if isinstance(pts, list) else 0
        order = " (corners in order around the sheet)" if kind == "a4" else ""
        raise StageError(f"scale reference: '{kind}' needs exactly {KINDS[kind]} points"
                         f"{order}, got {n}")  # fmt: skip
    points = []
    for k, p in enumerate(pts):
        pid = str(p.get("id", k + 1)) if isinstance(p, dict) else str(k + 1)
        obs = p.get("observations") if isinstance(p, dict) else None
        if not isinstance(obs, list):
            raise StageError(f"scale reference: point {pid}: 'observations' must be a list")
        parsed = []
        for o in obs:
            if not isinstance(o, dict) or not isinstance(o.get("frame"), str) or not o["frame"]:
                raise StageError(f"scale reference: point {pid}: each observation needs a "
                                 "'frame' file name")  # fmt: skip
            parsed.append(Observation(o["frame"], _num(o.get("x"), f"point {pid} x"),
                                      _num(o.get("y"), f"point {pid} y")))  # fmt: skip
        if len({o.frame for o in parsed}) < 2:
            raise StageError(f"scale reference: point {pid} needs observations in at least 2 "
                             f"different frames, got {len(parsed)}")  # fmt: skip
        points.append(RefPoint(pid, parsed))
    dist = doc.get("distance_m")
    if kind == "distance":
        dist = _num(dist, "distance_m")
        if dist <= 0:
            raise StageError(f"scale reference: distance_m must be > 0, got {dist}")
    elif dist is not None:
        raise StageError("scale reference: distance_m is only for kind 'distance'")
    return ScaleRef(kind, points, dist)


def load_reference(path: Path) -> ScaleRef:
    try:
        doc = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError) as e:
        raise StageError(f"scale reference {path}: cannot read it ({e})") from e
    return parse_reference(doc)


# ---------------------------------------------------------------- triangulation


def triangulate(poses: list[np.ndarray], xy: np.ndarray) -> np.ndarray:
    """Multi-view DLT. poses: 3x4 [R|t] cam_from_world; xy: (n, 2) undistorted normalized
    image coordinates (x/z, y/z). Returns the world point (3,)."""
    rows = []
    for P, (x, y) in zip(poses, xy, strict=True):
        rows += [x * P[2] - P[0], y * P[2] - P[1]]
    X = np.linalg.svd(np.array(rows))[2][-1]
    return np.asarray(X[:3] / X[3])


def triangulate_reference(
    rec: pycolmap.Reconstruction, ref: ScaleRef, frame_names: set[str]
) -> tuple[np.ndarray, float]:
    """Marked points in COLMAP world (k, 3) and the max reprojection error of the marks (px)."""
    by_name = {rec.images[i].name: i for i in rec.reg_image_ids()}
    out, worst = [], 0.0
    for p in ref.points:
        poses, xy, views = [], [], []
        for o in p.observations:
            if o.frame not in frame_names:
                raise StageError(f"scale reference: point {p.id}: unknown frame {o.frame!r} "
                                 "(not in this run's frames.json)")  # fmt: skip
            if o.frame not in by_name:
                raise StageError(f"scale reference: point {p.id}: frame {o.frame!r} is not "
                                 "registered (no camera pose); use another frame")  # fmt: skip
            im = rec.images[by_name[o.frame]]
            cam = rec.cameras[im.camera_id]
            if not (0 <= o.x <= cam.width and 0 <= o.y <= cam.height):
                raise StageError(f"scale reference: point {p.id}: ({o.x}, {o.y}) is outside "
                                 f"frame {o.frame} ({cam.width}x{cam.height})")  # fmt: skip
            P = np.asarray(im.cam_from_world().matrix())
            poses.append(P)
            xy.append(np.asarray(cam.cam_from_img(np.array([[o.x, o.y]])))[0])
            views.append((P, cam, (o.x, o.y)))
        X = triangulate(poses, np.array(xy))
        for P, cam, uv in views:
            xc = P[:, :3] @ X + P[:, 3]
            if xc[2] <= 0:
                raise StageError(f"scale reference: point {p.id} triangulates behind a camera; "
                                 "the marks do not show the same point")  # fmt: skip
            px = np.asarray(cam.img_from_cam(xc[None]))[0][:2]
            worst = max(worst, float(np.linalg.norm(px - uv)))
        out.append(X)
    return np.array(out), worst


# ---------------------------------------------------------------- scale


def distance_scale(p: np.ndarray, distance_m: float) -> float:
    d = float(np.linalg.norm(p[0] - p[1]))
    if d <= 0:
        raise StageError("scale reference: the two points triangulate to the same place")
    return distance_m / d


def a4_scale(c: np.ndarray) -> tuple[float, float]:
    """Least-squares scale from the 4 edges and 2 diagonals of corners c (4, 3) in order, and
    the RMS residual of the 6 lengths after scaling, in cm. The first edge may be either side."""
    d = np.array([np.linalg.norm(c[i] - c[j]) for i, j in
                  ((0, 1), (1, 2), (2, 3), (3, 0), (0, 2), (1, 3))])  # fmt: skip
    if not d.all():
        raise StageError("scale reference: A4 corners coincide")
    best = (0.0, math.inf)
    for a, b in ((A4_SHORT, A4_LONG), (A4_LONG, A4_SHORT)):
        L = np.array([a, b, a, b, A4_DIAG, A4_DIAG])
        s = float(d @ L / (d @ d))
        rms = float(np.sqrt(np.mean((s * d - L) ** 2)))
        if rms < best[1]:
            best = (s, rms)
    return best[0], round(best[1] * 100, 3)


# ---------------------------------------------------------------- gravity


def rotation_between(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Smallest rotation taking unit vector a to unit vector b (Rodrigues)."""
    v, c = np.cross(a, b), float(np.dot(a, b))
    if c < -1 + 1e-9:  # antiparallel: half-turn about any perpendicular axis
        axis = np.cross(a, [1.0, 0, 0] if abs(a[0]) < 0.9 else [0, 1.0, 0])
        axis /= np.linalg.norm(axis)
        return 2 * np.outer(axis, axis) - np.eye(3)
    K = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return np.eye(3) + K + K @ K / (1 + c)


def estimate_up(Rs: np.ndarray, centers: np.ndarray) -> np.ndarray:
    """Camera estimate of up in COLMAP world: mean of the cameras' -y axes (image up; upright
    phone or drone, as COLMAP's model_orientation_aligner); plane-fit normal if degenerate."""
    up = -Rs[:, 1, :].mean(axis=0)  # row 1 of R_cam_from_world = world direction of cam +y
    if np.linalg.norm(up) > 0.3 or len(centers) < 3:
        return np.asarray(up / (np.linalg.norm(up) or 1.0))
    # ponytail: nadir/planar flights only; sign taken from the weak mean, arbitrary if ~0
    _, _, vt = np.linalg.svd(centers - centers.mean(axis=0))
    n = vt[2]
    return np.asarray(n if np.dot(n, up) >= 0 else -n)


def fit_floor(
    xyz: np.ndarray, up: np.ndarray, centers: np.ndarray, seed: int = 0
) -> tuple[np.ndarray, float, int] | None:
    """RANSAC plane: the one with the most inliers among planes whose normal is within
    MAX_TILT_DEG of up and that lie below the mean camera height. Refit by SVD on its inliers.
    Returns (unit normal towards up, offset d of n.x = d, inliers) or None."""
    if len(xyz) < max(PLANE_MIN_POINTS, 3):
        return None
    rng = np.random.default_rng(seed)
    lo, hi = np.percentile(xyz, [2, 98], axis=0)
    tol = PLANE_TOL * float(np.linalg.norm(hi - lo))
    sample = xyz[rng.choice(len(xyz), min(RANSAC_SAMPLE, len(xyz)), replace=False)]
    tri = sample[rng.integers(0, len(sample), (RANSAC_ITERS, 3))]
    n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    norm = np.linalg.norm(n, axis=1)
    ok = norm > 0
    n[ok] /= norm[ok, None]
    n *= np.where(n @ up < 0, -1.0, 1.0)[:, None]
    d = (n * tri[:, 0]).sum(1)
    cos_max = math.cos(math.radians(MAX_TILT_DEG))
    ok &= (n @ up >= cos_max) & (d < (centers @ n.T).mean(0))
    if not ok.any():
        return None
    n, d = n[ok], d[ok]
    counts = np.concatenate([(np.abs(sample @ n[i : i + 64].T - d[i : i + 64]) < tol).sum(0)
                             for i in range(0, len(n), 64)])  # fmt: skip
    k = int(np.argmax(counts))
    if counts[k] < max(PLANE_MIN_POINTS, PLANE_MIN_FRAC * len(sample)):
        return None
    inl = xyz[np.abs(xyz @ n[k] - d[k]) < tol]
    nk = np.linalg.svd(inl - inl.mean(0), full_matrices=False)[2][2]
    nk = nk if nk @ up >= 0 else -nk
    if nk @ up < cos_max:
        return None
    return nk, float(inl.mean(0) @ nk), len(inl)


# ---------------------------------------------------------------- the similarity


@dataclass
class Alignment:
    """x' = s * A @ x + T, COLMAP world -> bundle world. info = bundle metadata (world.*)."""

    s: float
    A: np.ndarray
    T: np.ndarray
    info: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)

    def apply(self, x: np.ndarray) -> np.ndarray:
        return np.asarray(self.s * x @ self.A.T + self.T)

    def pose(self, R: np.ndarray, t: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """cam_from_world after the transform. Camera coordinates scale by s too, which leaves
        every projection (x/z, y/z) unchanged."""
        Rn = R @ self.A.T
        return Rn, self.s * t - Rn @ self.T

    def matrix(self) -> np.ndarray:
        W = np.eye(4)
        W[:3, :3], W[:3, 3] = self.s * self.A, self.T
        return W


def align(
    xyz: np.ndarray, Rs: np.ndarray, centers: np.ndarray, marked: np.ndarray | None = None,
    ref: ScaleRef | None = None,
) -> Alignment:  # fmt: skip
    """xyz: sparse points (COLMAP world); Rs: cam_from_world rotations of the registered
    cameras; centers: their centres; marked: triangulated reference points (with ref)."""
    cam_up = estimate_up(Rs, centers)
    floor = fit_floor(xyz, cam_up, centers)
    up = floor[0] if floor else cam_up
    A = rotation_between(up, np.array([0.0, 0, 1]))
    s, err = 1.0, None
    if ref is not None and marked is not None:
        if ref.kind == "distance":
            assert ref.distance_m is not None  # parse_reference guarantees it
            s = distance_scale(marked, ref.distance_m)
        else:
            s, err = a4_scale(marked)
    y = s * xyz @ A.T
    floor_z = s * floor[1] if floor else float(np.percentile(y[:, 2], 2))
    T = -np.array([y[:, 0].mean(), y[:, 1].mean(), floor_z])
    info = {
        "metric": ref is not None, "scale_source": ref.kind if ref else None,
        "scale_factor": s, "gravity_source": "plane" if floor else "cameras",
        "up_axis": UP_AXIS, "scale_error_cm": err,
    }  # fmt: skip
    corr = math.degrees(math.acos(min(1.0, float(up @ cam_up))))
    metrics = {**info, "gravity_correction_deg": round(corr, 2),
               "floor_inliers": floor[2] if floor else 0}  # fmt: skip
    return Alignment(s, A, T, info, metrics)
