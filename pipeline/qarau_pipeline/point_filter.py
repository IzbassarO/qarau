"""Point filter after mapping (ADR 0013): drop badly triangulated 3D points from the model.

Low parallax first (max triangulation angle < MIN_TRI_ANGLE_DEG), then reprojection error
above the mapper's filter_max_reproj_error. Each dropped point counts in exactly one bucket.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import pycolmap

MIN_TRI_ANGLE_DEG = 1.5
MAX_CENTERS = 40  # ponytail: longer tracks are subsampled evenly (780 pairs max per point)


@dataclass
class PointFilterCounts:
    points_total: int
    points_dropped_low_parallax: int
    points_dropped_reproj: int
    points_kept: int

    def asdict(self) -> dict[str, int]:
        return asdict(self)


def max_reproj_error_px() -> float:
    """The incremental mapper's own filter threshold (4 px); every incremental call in
    reconstruction.py keeps the default mapper filter options."""
    return float(pycolmap.IncrementalPipelineOptions().mapper.filter_max_reproj_error)


def max_tri_angle_deg(xyz: np.ndarray, centers: np.ndarray) -> float:
    """Largest angle at xyz between the rays to any two camera centres; 0 for fewer than 2."""
    if len(centers) > MAX_CENTERS:
        centers = centers[np.linspace(0, len(centers) - 1, MAX_CENTERS).astype(int)]
    rays = centers - xyz
    rays = rays / np.maximum(np.linalg.norm(rays, axis=1, keepdims=True), 1e-12)
    cos = float((rays @ rays.T).min()) if len(rays) > 1 else 1.0
    return float(np.degrees(np.arccos(np.clip(cos, -1.0, 1.0))))


def filter_points(
    rec: Any, max_reproj_px: float, min_angle_deg: float = MIN_TRI_ANGLE_DEG
) -> PointFilterCounts:
    """Delete low-parallax and high-error points from rec in place; return the counts."""
    rec.update_point_3d_errors()
    centers = {i: np.asarray(rec.image(i).projection_center()) for i in rec.reg_image_ids()}
    low, reproj = [], []
    for pid, p in rec.points3D.items():
        ids = sorted({e.image_id for e in p.track.elements} & centers.keys())
        c = np.array([centers[i] for i in ids]).reshape(-1, 3)
        if max_tri_angle_deg(np.asarray(p.xyz), c) < min_angle_deg:
            low.append(pid)
        elif p.error > max_reproj_px:
            reproj.append(pid)
    total = len(rec.points3D)
    for pid in low + reproj:
        rec.delete_point3D(pid)
    return PointFilterCounts(total, len(low), len(reproj), total - len(low) - len(reproj))
