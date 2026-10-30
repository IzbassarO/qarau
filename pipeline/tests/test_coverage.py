"""Coverage (COV1): the frustum test, the voxel split, one test per threshold, findings and a
synthetic run dir. Expected values are literals worked out by hand from the hand-placed points
and cameras."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from qarau_pipeline import workflow
from qarau_pipeline.coverage import (
    View,
    classify_regions,
    points_in_frustum,
    run_coverage,
    settings,
    voxelize,
)
from qarau_pipeline.interfaces import StageError
from qarau_pipeline.workflow import Step, StepContext, run_step

# A 100 x 100 pinhole camera, f 100, principal point (50, 50), at the origin looking along +z:
# u = 100 x / z + 50, v = 100 y / z + 50.
K = np.array([[100.0, 0, 50], [0, 100.0, 50], [0, 0, 1]])
EYE, ZERO = np.eye(3), np.zeros(3)
CAM = View(K, EYE, ZERO, 100, 100)

# ---------------------------------------------------------------- frustum


def test_point_inside_the_frustum_is_seen_behind_and_past_the_edge_are_not() -> None:
    pts = [[0, 0, 5], [0, 0, -5], [10, 0, 5]]  # inside; behind the camera; u = 250 > 100
    assert points_in_frustum(pts, K, EYE, ZERO, 100, 100, 0.01, None).tolist() == [
        True,
        False,
        False,
    ]


def test_frustum_image_edges_near_far_and_a_moved_camera() -> None:
    # x = -2.5 at z 5 -> u 0 (inside, [0, 100)); x = 2.5 -> u 100 (outside)
    pts = [[-2.5, 0, 5], [2.5, 0, 5], [0, 0, 5]]
    assert points_in_frustum(pts, K, EYE, ZERO, 100, 100, 0.01, None).tolist() == [
        True,
        False,
        True,
    ]
    assert points_in_frustum([[0, 0, 5]], K, EYE, ZERO, 100, 100, 0.01, 4.0).tolist() == [False]
    assert points_in_frustum([[0, 0, 5]], K, EYE, ZERO, 100, 100, 5.0, None).tolist() == [False]
    # a camera at z = -5 (t = +5 along z) sees the origin at depth 5
    t = np.array([0, 0, 5.0])
    assert points_in_frustum([[0, 0, 0]], K, EYE, t, 100, 100, 0.01, None).tolist() == [True]


def test_voxelize_8_points_gives_2_regions() -> None:
    pts = [[0.1, 0.2, 0.3], [0.9, 0.9, 0.9], [0.5, 0.1, 0.7], [0.0, 0.0, 0.0],
           [-0.5, 0.2, 0.3], [-0.1, 0.9, 0.1], [-1.0, 0.0, 0.0], [-0.7, 0.5, 0.99]]  # fmt: skip
    region, keys, bounds = voxelize(pts, 1.0)
    assert region.tolist() == [1, 1, 1, 1, 0, 0, 0, 0]
    assert keys.tolist() == [[-1, 0, 0], [0, 0, 0]]
    assert bounds.tolist() == [[[-1, 0, 0], [0, 1, 1]], [[0, 0, 0], [1, 1, 1]]]


# ---------------------------------------------------------------- thresholds

# The keys of config.yaml `coverage:` with fixed values, so each test moves exactly one of them.
BASE = {**workflow.load_config()["coverage"], "voxel_size": 1.0, "min_frames": 3,
        "min_frame_quality": 100.0, "min_points": 20, "near": 0.01, "far": None}  # fmt: skip


def cluster(n: int, x0: float = 0.1, x1: float = 0.9) -> np.ndarray:
    """n points on x in [x0, x1] at y 0.5, z 5.5: inside cell (0, 0, 5) of a 1.0 grid."""
    return np.stack([np.linspace(x0, x1, n), np.full(n, 0.5), np.full(n, 5.5)], 1)


def states(points: Any, n_views: int = 3, quality: list[float] | None = None,
           labels: list[int] | None = None, **over: Any) -> list[tuple[Any, ...]]:  # fmt: skip
    """(id, state, frame_count, point_count) per region; n_views copies of CAM, BASE + over."""
    pts = np.asarray(points, float)
    q = quality if quality is not None else [500.0] * n_views
    lab = labels if labels is not None else [0] * len(pts)
    regions = classify_regions(pts, dict.fromkeys(range(n_views), CAM), q, lab, {**BASE, **over})
    return [(r["id"], r["state"], r["frame_count"], r["point_count"]) for r in regions]


def test_min_frames_boundary() -> None:
    assert states(cluster(20), n_views=3) == [("0_0_5", "inspected_ok", 3, 20)]
    assert states(cluster(20), n_views=2) == [("0_0_5", "not_seen", 2, 20)]
    assert states(cluster(20), n_views=3, min_frames=4) == [("0_0_5", "not_seen", 3, 20)]


def test_min_points_boundary() -> None:
    assert states(cluster(20)) == [("0_0_5", "inspected_ok", 3, 20)]
    assert states(cluster(19)) == [("0_0_5", "not_seen", 3, 19)]
    assert states(cluster(20), min_points=21) == [("0_0_5", "not_seen", 3, 20)]


def test_min_frame_quality_boundary() -> None:
    q = [500.0, 500.0, 120.0]  # the third frame is the weakest
    assert states(cluster(20), quality=q, min_frame_quality=120.0) == [
        ("0_0_5", "inspected_ok", 3, 20)
    ]
    assert states(cluster(20), quality=q, min_frame_quality=120.5) == [("0_0_5", "not_seen", 2, 20)]
    # no score (an image set) counts; -inf (not kept, or in a bad quality issue) never does
    assert states(cluster(20), quality=[np.nan, 500.0, 500.0]) == [("0_0_5", "inspected_ok", 3, 20)]
    assert states(cluster(20), quality=[-np.inf, 500.0, 500.0]) == [("0_0_5", "not_seen", 2, 20)]


def test_voxel_size_boundary() -> None:
    # 19 points on x in [0.1, 0.9] and one at x 1.5: a cell of 1.5 puts that one in the next
    # cell (19 + 1 points: both not_seen), a cell of 1.6 holds all 20 (inspected_ok).
    pts = np.vstack([cluster(19), [[1.5, 0.5, 5.5]]])
    assert states(pts, voxel_size=1.5) == [("0_0_3", "not_seen", 3, 19),
                                           ("1_0_3", "not_seen", 3, 1)]  # fmt: skip
    assert states(pts, voxel_size=1.6) == [("0_0_3", "inspected_ok", 3, 20)]


def test_far_and_tracks_limit_what_a_frame_sees() -> None:
    assert states(cluster(20), far=5.5) == [("0_0_5", "not_seen", 0, 20)]
    # tracks: frame 1 never matched any of these points, so only frames 0 and 2 see them
    offsets = np.arange(0, 41, 2)
    ids = np.tile([0, 2], 20)
    regions = classify_regions(cluster(20), dict.fromkeys(range(3), CAM), [500.0] * 3,
                               [0] * 20, BASE, (offsets, ids))  # fmt: skip
    assert (regions[0]["state"], regions[0]["frame_count"]) == ("not_seen", 2)


def test_unregistered_frame_never_counts() -> None:
    views = {0: CAM, 2: CAM}  # frame 1 is not registered: no view
    regions = classify_regions(cluster(20), views, [500.0] * 3, [0] * 20, BASE)
    assert (regions[0]["state"], regions[0]["frame_count"]) == ("not_seen", 2)


def test_a_finding_point_makes_the_region_finding_even_unseen() -> None:
    lab = [0] * 20
    lab[3], lab[7] = 2, 1
    assert states(cluster(20), labels=lab) == [("0_0_5", "finding", 3, 20)]
    assert states(cluster(20), n_views=0, labels=lab) == [("0_0_5", "finding", 0, 20)]
    regions = classify_regions(cluster(20), {}, [], lab, BASE)
    assert regions[0]["finding_ids"] == [1, 2]
    assert states(cluster(20), n_views=0) == [("0_0_5", "not_seen", 0, 20)]  # never OK


# ---------------------------------------------------------------- settings


def test_config_yaml_has_the_coverage_section() -> None:
    cfg = workflow.load_config()["coverage"]
    assert set(cfg) == {"voxel_size", "min_frames", "min_frame_quality", "min_points", "near",
                        "far", "unusable_issues"}  # fmt: skip
    assert settings(workflow.load_config()) == cfg
    with pytest.raises(StageError, match="no `coverage:` section"):
        settings({})
    with pytest.raises(StageError, match="misses min_points"):
        settings({"coverage": {k: v for k, v in cfg.items() if k != "min_points"}})
    with pytest.raises(StageError, match="voxel_size must be > 0"):
        settings({"coverage": {**cfg, "voxel_size": 0}})
    with pytest.raises(StageError, match="coverage.far must be empty or a positive number"):
        settings({"coverage": {**cfg, "far": -1}})


# ---------------------------------------------------------------- a synthetic run dir

# voxel 1.0; regions (cell x, points, frames whose tracks hold them):
#   -8: 20 points, track frame 0 only, outside every frustum         -> not_seen, 0 frames
#    0: 20 points, frames 0-3                                          -> inspected_ok, 3
#    2: 20 points, frames 0-3, point 40 is finding 1                   -> finding, 3
#    3: 20 points, frames 0-1 only                                     -> not_seen, 2
#    5:  5 points, frames 0-3                                          -> not_seen (points), 3
# 4 PINHOLE 100x100 cameras (f 100) looking along +z from (1.0 / 1.5 / 2.0 / 2.5, 0.5, -10);
# frame 3 is inside a blur issue of the quality report, so 3 usable frames.
CELLS = [(-8, 20, [0]), (0, 20, [0, 1, 2, 3]), (2, 20, [0, 1, 2, 3]), (3, 20, [0, 1]),
         (5, 5, [0, 1, 2, 3])]  # fmt: skip
CAM_X = [1.0, 1.5, 2.0, 2.5]
CAMERA = {"width": 100, "height": 100, "model": "PINHOLE", "params": [100, 100, 50, 50],
          "K": [100, 100, 50, 50]}  # fmt: skip
FINDING_POINT = 40


def write_run(run: Path) -> None:
    pts, links = [], []
    for x, n, tracked in CELLS:
        for k in range(n):
            pts.append([x + 0.1 + 0.2 * (k % 5), 0.2 + 0.15 * (k // 5), 0.5])
            links.append(tracked)
    web = run / "export" / "web"
    web.mkdir(parents=True)
    np.array(pts, "<f4").tofile(web / "points.position.f32")
    np.cumsum([0] + [len(r) for r in links]).astype("<u4").tofile(web / "po.u32")
    np.array([f for r in links for f in r], "<u2").tofile(web / "pf.u16")
    frames = [{"i": i, "image": f"frames/{i:06d}.jpg", "kept": True, "registered": True,
               "blur": 500.0, "camera": CAMERA,
               "pose": {"qvec": [1, 0, 0, 0], "tvec": [-x, -0.5, 10.0]}}
              for i, x in enumerate(CAM_X)]  # fmt: skip
    man = {"version": 2,
           "points": {"count": len(pts), "position": "export/web/points.position.f32"},
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


EXPECTED_REGIONS = [
    {"id": "-8_0_0", "bounds": {"min": [-8, 0, 0], "max": [-7, 1, 1]}, "state": "not_seen",
     "frame_count": 0, "point_count": 20, "finding_ids": []},
    {"id": "0_0_0", "bounds": {"min": [0, 0, 0], "max": [1, 1, 1]}, "state": "inspected_ok",
     "frame_count": 3, "point_count": 20, "finding_ids": []},
    {"id": "2_0_0", "bounds": {"min": [2, 0, 0], "max": [3, 1, 1]}, "state": "finding",
     "frame_count": 3, "point_count": 20, "finding_ids": [1]},
    {"id": "3_0_0", "bounds": {"min": [3, 0, 0], "max": [4, 1, 1]}, "state": "not_seen",
     "frame_count": 2, "point_count": 20, "finding_ids": []},
    {"id": "5_0_0", "bounds": {"min": [5, 0, 0], "max": [6, 1, 1]}, "state": "not_seen",
     "frame_count": 3, "point_count": 5, "finding_ids": []},
]  # fmt: skip


def test_regions_json_schema_on_a_synthetic_run(tmp_path: Path) -> None:
    write_run(tmp_path)
    config = workflow.load_config()  # the shipped thresholds (voxel 1.0, 3 frames, 20 points)
    metrics = run_coverage(tmp_path, config)
    path = tmp_path / "coverage" / "regions.json"
    doc = json.loads(path.read_text())
    assert list(doc) == ["version", "units", "voxel_size", "thresholds", "summary", "regions"]
    assert (doc["version"], doc["units"], doc["voxel_size"]) == (1, "bundle", 1.0)
    assert doc["thresholds"] == {
        "min_frames": 3, "min_frame_quality": 100.0, "min_points": 20, "near": 0.01, "far": None,
        "unusable_issues": ["blur", "too_fast", "low_texture"],
    }  # fmt: skip
    assert doc["summary"] == {"inspected_ok": 1, "finding": 1, "not_seen": 3}
    assert doc["regions"] == EXPECTED_REGIONS
    assert metrics == {"regions": 5, "inspected_ok": 1, "finding": 1, "not_seen": 3}
    assert sorted(p.name for p in path.parent.iterdir()) == ["regions.json"]  # no temp file left

    before = path.read_bytes()
    run_coverage(tmp_path, config)  # idempotent: the same file again
    assert path.read_bytes() == before
    run_coverage(tmp_path, config, tmp_path / "elsewhere")  # --out: the same doc, written there
    assert (tmp_path / "elsewhere" / "coverage" / "regions.json").read_bytes() == before


def test_manual_finding_points_replace_the_project_labels(tmp_path: Path) -> None:
    write_run(tmp_path)
    config = workflow.load_config()
    out = tmp_path / "manual"
    run_coverage(tmp_path, config, out, finding_points={7: [FINDING_POINT]})
    regions = json.loads((out / "coverage" / "regions.json").read_text())["regions"]
    assert regions[2] == {**EXPECTED_REGIONS[2], "finding_ids": [7]}
    assert regions[:2] + regions[3:] == EXPECTED_REGIONS[:2] + EXPECTED_REGIONS[3:]
    metrics = run_coverage(tmp_path, config, out, finding_points={})  # no findings: none marked
    assert metrics == {"regions": 5, "inspected_ok": 2, "finding": 0, "not_seen": 3}


def test_workflow_step_and_its_plain_failure(tmp_path: Path) -> None:
    step = Step("coverage", workflow.coverage)
    config = workflow.load_config()
    res = run_step(step, StepContext(tmp_path, config, log=lambda _: None))
    assert res.status == "failed"
    assert res.error == ("StageError: coverage needs the map: no viewer bundle (the export step "
                         "has not run)")  # fmt: skip
    write_run(tmp_path)
    res = run_step(step, StepContext(tmp_path, config, force=True, log=lambda _: None))
    assert res.status == "ok"
    assert res.outputs == ["coverage/regions.json", "export/web/points.coverage.u8"]
    assert (res.metrics["inspected_ok"], res.metrics["finding"], res.metrics["not_seen"]) == (
        1,
        1,
        3,
    )
    assert json.loads((tmp_path / "coverage" / "step.json").read_text())["status"] == "done"
    # COV1-b: the step adds the per-point coverage array to the bundle it read
    man = json.loads((tmp_path / "export" / "web" / "manifest.json").read_text())
    assert man["coverage"]["computed"] is True and man["coverage"]["count"] == 85
    assert (tmp_path / "export/web/points.coverage.u8").stat().st_size == 85


def test_stale_findings_fail_in_plain_words(tmp_path: Path) -> None:
    write_run(tmp_path)
    np.zeros(3, "<u2").tofile(tmp_path / "findings" / "points.finding.u16")
    with pytest.raises(StageError, match="has 3 points, the map has 85"):
        run_coverage(tmp_path, workflow.load_config())
