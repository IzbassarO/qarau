from pathlib import Path

import numpy as np
import pycolmap
import pytest

from qarau_pipeline.point_filter import (
    PointFilterCounts,
    filter_points,
    max_reproj_error_px,
    max_tri_angle_deg,
)

F, CX, CY = 500.0, 320.0, 240.0
# cameras 0 and 1 nearly coincide
CENTERS: list[list[float]] = [[0, 0, 0], [0.01, 0, 0], [1, 0, 0], [2, 0, 0]]


Points = dict[int, tuple[list[float], list[int], float]]


def write_model(tmp: Path, points: Points) -> Path:
    """COLMAP text model, identity rotations. points: id -> (xyz, image ids, pixel offset
    added to every observation, i.e. its reprojection error)."""
    obs: dict[int, list[tuple[float, float, int]]] = {i: [] for i in range(len(CENTERS))}
    tracks = {}
    for pid, (xyz, imgs, off) in points.items():
        tr = []
        for i in imgs:
            x, y, z = np.subtract(xyz, CENTERS[i])
            obs[i].append((F * x / z + CX + off, F * y / z + CY, pid))
            tr.append(f"{i + 1} {len(obs[i]) - 1}")
        tracks[pid] = tr
    d = tmp / "sparse"
    d.mkdir()
    (d / "cameras.txt").write_text(f"1 PINHOLE 640 480 {F} {F} {CX} {CY}\n")
    lines = []
    for i, c in enumerate(CENTERS):
        lines.append(f"{i + 1} 1 0 0 0 {-c[0]} {-c[1]} {-c[2]} 1 f{i}.jpg")
        lines.append(" ".join(f"{u} {v} {p}" for u, v, p in obs[i]))
    (d / "images.txt").write_text("\n".join(lines) + "\n")
    (d / "points3D.txt").write_text(
        "".join(
            f"{pid} {xyz[0]} {xyz[1]} {xyz[2]} 1 2 3 0 {' '.join(tracks[pid])}\n"
            for pid, (xyz, _, _) in points.items()
        )
    )
    return d


def test_exact_counts_per_bucket(tmp_path: Path) -> None:
    good: Points = {pid: ([0.2 * pid - 1, 0.1, 5.0], [0, 2, 3], 0.0) for pid in range(1, 6)}
    points: Points = {
        **good,
        10: ([0.0, 0.0, 5.0], [0, 1], 0.0),  # 0.1 deg: low parallax
        11: ([0.5, 0.2, 5.0], [0, 2, 3], 20.0),  # 20 px off in every view
        12: ([0.3, 0.0, 5.0], [0, 1], 20.0),  # both: counted once, as low parallax
        13: ([1.0, 0.0, 5.0], [2, 3], 3.0),  # 3 px: under the 4 px threshold, kept
    }
    rec = pycolmap.Reconstruction(str(write_model(tmp_path, points)))
    assert rec.num_points3D() == 9
    counts = filter_points(rec, max_reproj_error_px())
    assert counts == PointFilterCounts(
        points_total=9, points_dropped_low_parallax=2, points_dropped_reproj=1, points_kept=6
    )
    assert sorted(rec.points3D) == [1, 2, 3, 4, 5, 13]


def test_threshold_is_the_mappers() -> None:
    assert max_reproj_error_px() == 4.0  # pycolmap's filter_max_reproj_error default


def test_max_tri_angle() -> None:
    xyz = np.array([0.0, 0, 1])
    assert max_tri_angle_deg(xyz, np.array([[-1.0, 0, 0], [1, 0, 0]])) == pytest.approx(90)
    assert max_tri_angle_deg(xyz, np.array([[1.0, 0, 0]])) == 0
    # long tracks are subsampled; the end cameras (widest baseline) are always kept
    line = np.stack([np.linspace(-1, 1, 101), np.zeros(101), np.zeros(101)], 1)
    assert max_tri_angle_deg(xyz, line) == pytest.approx(90)
