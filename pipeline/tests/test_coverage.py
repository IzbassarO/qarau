"""Coverage (COV1): the frustum test, the voxel split, the thresholds and a synthetic run dir.
Expected values are literals worked out by hand from the hand-placed points and cameras."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from qarau_pipeline import workflow
from qarau_pipeline.coverage import (
    View,
    classify_regions,
    point_in_frustum,
    run_coverage,
    settings,
    voxel_regions,
)
from qarau_pipeline.interfaces import StageError
from qarau_pipeline.workflow import Step, StepContext, run_step

K = np.array([[100.0, 0, 50], [0, 100.0, 50], [0, 0, 1]])
EYE, ZERO = np.eye(3), np.zeros(3)


# ---------------------------------------------------------------- frustum


def test_point_in_frustum_camera_at_origin_looking_along_z() -> None:
    pts = np.array([[0, 0, 5], [10, 0, 5], [0, 0, -5]], float)
    assert point_in_frustum(pts, EYE, ZERO, K, 100, 100).tolist() == [True, False, False]


def test_point_in_frustum_edges_and_far_plane() -> None:
    # u = 100 x / z + 50: x = -2.5 at z 5 -> u 0 (inside), x = 2.5 -> u 100 (outside: [0, 100))
    pts = np.array([[-2.5, 0, 5], [2.5, 0, 5], [0, 0, 5]], float)
    assert point_in_frustum(pts, EYE, ZERO, K, 100, 100).tolist() == [True, False, True]
    assert point_in_frustum(pts[2:], EYE, ZERO, K, 100, 100, far=4.0).tolist() == [False]
    # a camera moved to z = -5 (t = +5 along z) sees the origin at depth 5
    assert point_in_frustum([[0, 0, 0]], EYE, np.array([0, 0, 5.0]), K, 100, 100).tolist() == [True]


# ---------------------------------------------------------------- voxels


def test_voxel_split_of_8_points_gives_2_regions() -> None:
    pts = np.array([[0.1, 0.2, 0.3], [0.9, 0.9, 0.9], [0.5, 0.1, 0.7], [0.0, 0.0, 0.0],
                    [-0.5, 0.2, 0.3], [-0.1, 0.9, 0.1], [-1.0, 0.0, 0.0], [-0.7, 0.5, 0.99]])  # fmt: skip
    region, keys, bounds = voxel_regions(pts, 1.0)
    assert region.tolist() == [1, 1, 1, 1, 0, 0, 0, 0]
    assert keys.tolist() == [[-1, 0, 0], [0, 0, 0]]
    assert bounds.tolist() == [[[-1, 0, 0], [0, 1, 1]], [[0, 0, 0], [1, 1, 1]]]


# ---------------------------------------------------------------- thresholds


def classify(n_views: int = 3, usable: list[bool] | None = None, n_points: int = 20,
             finding: bool = False, far: float | None = None,
             observed: np.ndarray | None = None) -> tuple[int, int]:  # fmt: skip
    """One region with its centroid at (0, 0, 5), in front of n_views cameras at the origin;
    min_frames 3, min_points 20."""
    views: list[View | None] = [View(EYE, ZERO, K, 100, 100)] * n_views
    use = np.array(usable if usable is not None else [True] * n_views, bool)
    codes, count, _ = classify_regions(
        np.array([[0, 0, 5.0]]), np.array([n_points]), np.array([finding]), views, use, 3, 20,
        far, observed,
    )  # fmt: skip
    return int(codes[0]), int(count[0])


def test_three_usable_frames_and_enough_points_is_inspected_ok() -> None:
    assert classify() == (1, 3)


def test_two_frames_is_not_seen() -> None:
    assert classify(n_views=2) == (0, 2)


def test_three_frames_one_unusable_is_not_seen() -> None:
    assert classify(usable=[True, False, True]) == (0, 2)


def test_too_few_points_is_not_seen() -> None:
    assert classify(n_points=19) == (0, 3)


def test_a_finding_point_makes_the_region_finding() -> None:
    assert classify(finding=True) == (2, 3)
    assert classify(n_views=0, finding=True) == (2, 0)


def test_beyond_max_view_distance_or_not_in_the_tracks_does_not_count() -> None:
    assert classify(far=4.0) == (0, 0)
    assert classify(observed=np.array([[True, False, True]])) == (0, 2)


def test_unregistered_frame_never_counts() -> None:
    codes, count, _ = classify_regions(
        [[0, 0, 5]], np.array([20]), np.array([False]),
        [View(EYE, ZERO, K, 100, 100), None, View(EYE, ZERO, K, 100, 100)],
        np.array([True, True, True]), 3, 20,
    )  # fmt: skip
    assert (int(codes[0]), int(count[0])) == (0, 2)


# ---------------------------------------------------------------- a synthetic run dir

# voxel 1.0; regions (cell x, points, frames whose tracks hold them):
#   -8: 20 points, track frame 0 only, outside every frustum         -> not_seen, 0 frames
#    0: 20 points, frames 0-3                                          -> inspected_ok, 3
#    2: 20 points, frames 0-3, point 40 is a finding                   -> finding, 3
#    3: 20 points, frames 0-1 only                                     -> not_seen, 2
#    5:  5 points, frames 0-3                                          -> not_seen (points), 3
# 4 PINHOLE 100x100 cameras (f 100) looking along +z from (1.0 / 1.5 / 2.0 / 2.5, 0.5, -10);
# frame 3 is inside a blur issue of the quality report, so 3 usable frames.
CELLS = [(-8, 20, [0]), (0, 20, [0, 1, 2, 3]), (2, 20, [0, 1, 2, 3]), (3, 20, [0, 1]),
         (5, 5, [0, 1, 2, 3])]  # fmt: skip
CAM_X = [1.0, 1.5, 2.0, 2.5]
CAMERA = {"width": 100, "height": 100, "model": "PINHOLE", "params": [100, 100, 50, 50]}
FINDING_POINT = 40


def write_run(run: Path) -> None:
    pts, links = [], []
    for x, n, frames in CELLS:
        for k in range(n):
            pts.append([x + 0.1 + 0.2 * (k % 5), 0.2 + 0.15 * (k // 5), 0.5])
            links.append(frames)
    web = run / "export" / "web"
    web.mkdir(parents=True)
    np.array(pts, "<f4").tofile(web / "points.position.f32")
    np.cumsum([0] + [len(r) for r in links]).astype("<u4").tofile(web / "po.u32")
    np.array([f for r in links for f in r], "<u2").tofile(web / "pf.u16")
    frames = [{"i": i, "image": f"frames/{i:06d}.jpg", "kept": True, "registered": True,
               "camera": CAMERA, "pose": {"qvec": [1, 0, 0, 0], "tvec": [-x, -0.5, 10.0]}}
              for i, x in enumerate(CAM_X)]  # fmt: skip
    man = {"version": 2, "points": {"count": len(pts), "position": "export/web/points.position.f32"},
           "tracks": {"point_offsets": "export/web/po.u32", "point_frames": "export/web/pf.u16"},
           "frames": frames}  # fmt: skip
    (web / "manifest.json").write_text(json.dumps(man))
    (run / "frames").mkdir()
    rep = {"issues": [{"kind": "blur", "frames": [3]}, {"kind": "pure_rotation", "frames": [0]}]}
    (run / "frames" / "quality_report.json").write_text(json.dumps(rep))
    (run / "findings").mkdir()
    lab = np.zeros(len(pts), "<u2")
    lab[FINDING_POINT] = 1
    lab.tofile(run / "findings" / "points.finding.u16")


CONFIG = {"coverage": {"voxel_size": 1.0, "min_frames": 3, "min_points_per_region": 20}}
EXPECTED_REGIONS = [
    {"id": "-8_0_0", "bounds": [[-8, 0, 0], [-7, 1, 1]], "state": "not_seen", "frame_count": 0,
     "point_count": 20, "nearest_frames": ["000000.jpg", "000001.jpg", "000002.jpg"]},
    {"id": "0_0_0", "bounds": [[0, 0, 0], [1, 1, 1]], "state": "inspected_ok", "frame_count": 3,
     "point_count": 20, "nearest_frames": ["000000.jpg", "000001.jpg", "000002.jpg"]},
    {"id": "2_0_0", "bounds": [[2, 0, 0], [3, 1, 1]], "state": "finding", "frame_count": 3,
     "point_count": 20, "nearest_frames": ["000003.jpg", "000002.jpg", "000001.jpg"]},
    {"id": "3_0_0", "bounds": [[3, 0, 0], [4, 1, 1]], "state": "not_seen", "frame_count": 2,
     "point_count": 20, "nearest_frames": ["000001.jpg", "000000.jpg"]},
    {"id": "5_0_0", "bounds": [[5, 0, 0], [6, 1, 1]], "state": "not_seen", "frame_count": 3,
     "point_count": 5, "nearest_frames": ["000003.jpg", "000002.jpg", "000001.jpg"]},
]  # fmt: skip
EXPECTED_TOTALS = {"inspected_ok": {"count": 1, "percent": 20.0},
                   "finding": {"count": 1, "percent": 20.0},
                   "not_seen": {"count": 3, "percent": 60.0}}  # fmt: skip


def test_synthetic_run_writes_regions_and_the_point_array(tmp_path: Path) -> None:
    write_run(tmp_path)
    metrics = run_coverage(tmp_path, CONFIG)
    doc = json.loads((tmp_path / "coverage" / "regions.json").read_text())
    assert doc["schema_version"] == 1 and doc["voxel_size"] == 1.0
    assert doc["thresholds"]["min_frames"] == 3 and doc["thresholds"]["min_points_per_region"] == 20
    assert doc["quality_checked"] is True
    assert doc["frames"] == {"registered": 4, "usable": 3}
    assert doc["regions"] == EXPECTED_REGIONS
    assert doc["totals"] == EXPECTED_TOTALS
    assert metrics == {"regions": 5, "usable_frames": 3, "quality_checked": True,
                       "inspected_ok": 1, "finding": 1, "not_seen": 3}  # fmt: skip

    man = json.loads((tmp_path / "export/web/manifest.json").read_text())
    assert man["points"]["coverage"] == "export/web/points.coverage.u8"
    assert man["points"]["position"] == "export/web/points.position.f32"  # unchanged
    codes = np.fromfile(tmp_path / "export/web/points.coverage.u8", "u1")
    assert codes.tolist() == [0] * 20 + [1] * 20 + [2] * 20 + [0] * 20 + [0] * 5

    before = (tmp_path / "coverage" / "regions.json").read_bytes()
    run_coverage(tmp_path, CONFIG)  # idempotent: the same outputs again
    assert (tmp_path / "coverage" / "regions.json").read_bytes() == before


def test_workflow_step_and_its_plain_failure(tmp_path: Path) -> None:
    step = Step("coverage", workflow.coverage)
    res = run_step(step, StepContext(tmp_path, {}, log=lambda _: None))
    assert res.status == "failed"
    assert res.error == ("StageError: coverage needs the map: no viewer bundle (the export step "
                         "has not run)")  # fmt: skip
    write_run(tmp_path)
    res = run_step(step, StepContext(tmp_path, CONFIG, force=True, log=lambda _: None))
    assert res.status == "ok" and res.outputs == ["coverage/regions.json"]
    assert (res.metrics["inspected_ok"], res.metrics["finding"], res.metrics["not_seen"]) == (1, 1, 3)
    assert json.loads((tmp_path / "coverage" / "step.json").read_text())["status"] == "done"


def test_stale_findings_fail_in_plain_words(tmp_path: Path) -> None:
    write_run(tmp_path)
    np.zeros(3, "<u2").tofile(tmp_path / "findings" / "points.finding.u16")
    with pytest.raises(StageError, match="has 3 points, the map has 85"):
        run_coverage(tmp_path, CONFIG)


def test_config_yaml_has_the_coverage_block() -> None:
    cfg = workflow.load_config()["coverage"]
    assert set(cfg) == {"voxel_size", "min_frames", "min_points_per_region", "max_view_distance",
                        "unusable_issues"}  # fmt: skip
    assert settings(workflow.load_config())["unusable_issues"] == ["blur", "too_fast", "low_texture"]
    with pytest.raises(StageError, match="coverage.voxel_size must be a positive number"):
        settings({"coverage": {"voxel_size": 0}})
    with pytest.raises(StageError, match="coverage.min_frames must be an integer >= 1"):
        settings({"coverage": {"min_frames": 0}})
