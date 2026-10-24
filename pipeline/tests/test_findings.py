"""Manual finding geometry on a synthetic bundle: a 3 x 2 unit grid on z = 0 (points 0..11,
index = 4 * yi + xi), two off-plane points (12 above, 13 below) and one in-plane point outside
the polygon (14); 4 PINHOLE 100 x 100 cameras with hand-set poses and hand-written links."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from qarau_pipeline.findings import (
    Bundle,
    Camera,
    clip_to_rect,
    convex_hull,
    evidence_for,
    load_bundle,
    project_outline,
    rank_frames,
    region_area,
    select_indices,
    select_points,
)

GRID = [[x, y, 0.0] for y in range(3) for x in range(4)]
POINTS = GRID + [[1.5, 1.0, 1.0], [1.5, 1.0, -1.0], [5.0, 1.0, 0.0]]
POLYGON = [[-0.5, -0.5, 0], [3.5, -0.5, 0], [3.5, 2.5, 0], [-0.5, 2.5, 0]]
S = 0.7071067812
# camera 0 looks straight down from (1.5, 1, 10); 1 the same from 0.5 higher (same viewpoint);
# 2 from (9.5, 1, 8) at 45 degrees; 3 from (1.5, 9, 8) at 45 degrees from the other side
CAMS = [
    ([0.0, 1.0, 0.0, 0.0], [-1.5, 1.0, 10.0]),
    ([0.0, 1.0, 0.0, 0.0], [-1.5, 1.0, 10.5]),
    ([0.0, 0.9238795325, 0.0, -0.3826834324], [-1.0606601718, 1.0, 12.3743686708]),
    ([0.3826834324, -0.9238795325, 0.0, 0.0], [-1.5, 0.7071067812, 12.0208152802]),
]
# frames of each point: 0 and 1 see the whole grid, 2 sees 8 grid points, 3 sees 6
LINKS = [[0, 1], [0, 1, 2], [0, 1, 2], [0, 1, 2], [0, 1, 3], [0, 1, 2, 3], [0, 1, 2, 3],
         [0, 1, 2, 3], [0, 1, 3], [0, 1, 2, 3], [0, 1, 2], [0, 1], [1], [1], [1, 2]]  # fmt: skip
CAMERA = {"width": 100, "height": 100, "model": "PINHOLE", "params": [100, 100, 50, 50]}


def write_bundle(run: Path) -> None:
    (run / "web").mkdir(parents=True)
    np.array(POINTS, "<f4").tofile(run / "web/points.position.f32")
    np.cumsum([0] + [len(r) for r in LINKS]).astype("<u4").tofile(run / "web/po.u32")
    np.array([f for r in LINKS for f in r], "<u2").tofile(run / "web/pf.u16")
    frames = [{"i": i, "image": f"frames/{i:06d}.jpg", "registered": True, "camera": CAMERA,
               "pose": {"qvec": q, "tvec": t}} for i, (q, t) in enumerate(CAMS)]  # fmt: skip
    man = {"version": 2, "points": {"count": len(POINTS), "position": "web/points.position.f32"},
           "tracks": {"point_offsets": "web/po.u32", "point_frames": "web/pf.u16"},
           "frames": frames}  # fmt: skip
    (run / "web/manifest.json").write_text(json.dumps(man))


@pytest.fixture
def bundle(tmp_path: Path) -> Bundle:
    write_bundle(tmp_path)
    return load_bundle(tmp_path)


def test_load_bundle(bundle: Bundle) -> None:
    assert bundle.positions.shape == (15, 3) and bundle.offsets.tolist()[:4] == [0, 2, 5, 8]
    assert bundle.frame_ids.tolist()[:5] == [0, 1, 0, 1, 2]
    assert sorted(bundle.cameras) == [0, 1, 2, 3] and bundle.images[2] == "frames/000002.jpg"
    assert bundle.cameras[0].center.tolist() == pytest.approx([1.5, 1.0, 10.0])
    assert bundle.cameras[2].center.tolist() == pytest.approx([9.5, 1.0, 8.0])


def test_select_points_keeps_the_grid_only(bundle: Bundle) -> None:
    assert select_points(bundle.positions, POLYGON).tolist() == list(range(12))
    # a looser tolerance lets the off-plane points in; a triangle picks a corner only
    assert select_points(bundle.positions, POLYGON, tol=1.0).tolist() == [*range(12), 12, 13]
    tri = [[-0.5, -0.5, 0], [1.8, -0.5, 0], [-0.5, 1.8, 0]]
    assert select_points(bundle.positions, tri).tolist() == [0, 1, 4]
    # a tilted polygon selects the points near its own plane
    tilted = [[-0.5, -0.5, -1], [3.5, -0.5, -1], [3.5, 2.5, 1], [-0.5, 2.5, 1]]
    assert select_points(bundle.positions, tilted).tolist() == [4, 5, 6, 7]


FINE_GRID = np.array([[x / 10, y / 10, 0.0] for y in range(10) for x in range(10)])  # i = 10y + x
SQUARE_045 = [[0, 0, 0], [0.45, 0, 0], [0.45, 0.45, 0], [0, 0.45, 0]]
INSIDE_045 = [0, 1, 2, 3, 4, 10, 11, 12, 13, 14, 20, 21, 22, 23, 24, 30, 31, 32, 33, 34,
              40, 41, 42, 43, 44]  # fmt: skip


def test_square_polygon_on_the_fine_grid_selects_the_25_known_points() -> None:
    assert select_points(FINE_GRID, SQUARE_045).tolist() == INSIDE_045
    assert select_indices(len(FINE_GRID), INSIDE_045[::-1]).tolist() == INSIDE_045


def test_unit_square_area_is_one() -> None:
    square = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0.0], [0.5, 0.5, 0]])
    assert region_area(square) == pytest.approx(1.0, abs=1e-6)
    assert region_area(FINE_GRID[INSIDE_045]) == pytest.approx(0.16, abs=1e-6)  # 0.4 x 0.4


@pytest.mark.parametrize(
    "bad",
    [[[0, 0, 0], [1, 1, 1]], [[0, 0, 0], [1, 0, 0], [2, 0, 0]], [[0, 0], [1, 0], [0, 1]]],
)
def test_select_points_rejects_bad_polygons(bundle: Bundle, bad: list[list[float]]) -> None:
    with pytest.raises(ValueError):
        select_points(bundle.positions, bad)


def test_select_indices_validates() -> None:
    assert select_indices(15, [3, 1, 2]).tolist() == [1, 2, 3]
    for bad in ([], [15], [-1], [1, 1], [1.5], [True]):
        with pytest.raises(ValueError):
            select_indices(15, bad)


def test_region_area_of_the_grid_is_six(bundle: Bundle) -> None:
    assert region_area(bundle.positions[:12]) == pytest.approx(6.0, abs=1e-6)
    assert region_area(bundle.positions[[0, 1]]) == 0.0
    assert region_area(bundle.positions[[0, 1, 2]]) == 0.0  # collinear
    assert region_area(bundle.positions[[0, 3, 11]]) == pytest.approx(3.0, abs=1e-6)
    # the plane is the points' own: a tilted triangle keeps its true area
    assert region_area(np.array([[0, 0, 0], [2, 0, 0], [0, 2, 2.0]])) == pytest.approx(2 * 2**0.5)


def test_convex_hull_and_clip() -> None:
    hull = convex_hull(np.array([[0, 0], [2, 0], [1, 0.5], [2, 2], [0, 2], [1, 1]]))
    assert hull.tolist() == [[0, 0], [2, 0], [2, 2], [0, 2]]
    assert convex_hull(np.array([[0, 0], [1, 1], [2, 2]])).tolist() == [[0, 0], [2, 2]]
    clipped = clip_to_rect(np.array([[-1, -1], [3, -1], [3, 3], [-1, 3.0]]), 2, 2)
    assert sorted(clipped.tolist()) == [[0, 0], [0, 2], [2, 0], [2, 2]]
    assert len(clip_to_rect(np.array([[5, 5], [6, 5], [6, 6.0]]), 2, 2)) == 0


def test_rank_frames_most_seen_then_spread(bundle: Bundle) -> None:
    sel = np.arange(12)
    # 0 and 1 both see all 12 (0 wins the tie); 1 shares 0's viewpoint, so 2 and 3 follow
    assert rank_frames(sel, bundle) == [0, 2, 3]
    assert rank_frames(sel, bundle, k=4) == [0, 2, 3, 1]
    assert rank_frames(sel, bundle, k=1) == [0]
    assert rank_frames(np.array([12, 13]), bundle) == [1]  # only frame 1 sees them


def test_project_outline_literal_pixels(bundle: Bundle) -> None:
    out = project_outline(bundle.positions[:12], bundle.cameras[0])
    np.testing.assert_allclose(out, [[35, 40], [65, 40], [65, 60], [35, 60]], atol=0.5)
    # a camera at (0, 0, 5) looking along +z has the grid (z = 0) behind it
    behind = Camera(np.eye(3), np.array([0, 0, -5.0]), "PINHOLE", [100, 100, 50, 50], 100, 100)
    assert project_outline(bundle.positions[:12], behind) == []
    assert project_outline(bundle.positions[:2], bundle.cameras[0]) == []


def test_evidence_frames_and_outlines(bundle: Bundle) -> None:
    ev = evidence_for(bundle, np.arange(12))
    assert [e["frame"] for e in ev] == [0, 2, 3]
    assert [e["image"] for e in ev] == ["frames/000000.jpg", "frames/000002.jpg",
                                        "frames/000003.jpg"]  # fmt: skip
    for e in ev:
        assert len(e["outline"]) >= 3
        assert all(0 <= u <= 100 and 0 <= v <= 100 for u, v in e["outline"])
