"""Deterministic mask -> 3D tests on a synthetic plane scene (no ML)."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest

from qarau_pipeline.export import project, qvec_from_matrix
from qarau_pipeline.interfaces import StageError
from qarau_pipeline.projection import components, project_stage
from qarau_pipeline.segmentation import (
    ManualMasks,
    SamApiSegmenter,
    parse_prompt,
    segment_stage,
)

W, H, F = 640, 480, 500.0
SPACING = 0.05
A = (0.5, 1.5, 0.5, 1.5)  # region seen by frames 0-2: 1 m x 1 m
B = (3.0, 3.5, 3.0, 3.5)  # region seen by frame 3 only: 0.5 m x 0.5 m
AZ = [0, 40, 80, 200]


def _pose(az_deg: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    a = np.radians(az_deg)
    target = np.array([2.0, 2.0, 0.0])
    c = target + 6 * np.array([np.sin(a) * 0.7, -np.cos(a) * 0.7, 0.7])
    fwd = (target - c) / np.linalg.norm(target - c)
    right = np.cross(fwd, [0, 0, 1.0])
    right /= np.linalg.norm(right)
    R = np.stack([right, np.cross(fwd, right), fwd])
    return R, -R @ c, c


def _quad_mask(R: np.ndarray, t: np.ndarray, region: tuple[float, float, float, float]):
    x0, x1, y0, y1 = region
    q = np.array([[x0, y0, 0], [x1, y0, 0], [x1, y1, 0], [x0, y1, 0]], float) @ R.T + t
    px = project("PINHOLE", [F, F, W / 2, H / 2], q)
    m = np.zeros((H, W), np.uint8)
    cv2.fillConvexPoly(m, np.round(px).astype(np.int32), 255)
    return m


@pytest.fixture
def scene(tmp_path: Path) -> dict[str, Any]:
    run = tmp_path / "run"
    (run / "web").mkdir(parents=True)
    (run / "frames").mkdir()
    g = np.arange(0, 4 + 1e-9, SPACING)
    pos = np.array([[x, y, 0.0] for x in g for y in g], np.float32)
    frames, fpts = [], []
    for i, az in enumerate(AZ):
        R, t, c = _pose(az)
        xc = pos.astype(float) @ R.T + t
        px = project("PINHOLE", [F, F, W / 2, H / 2], xc)
        vis = (xc[:, 2] > 0) & (px[:, 0] >= 0) & (px[:, 0] < W) & (px[:, 1] >= 0) & (px[:, 1] < H)
        fpts.append(np.nonzero(vis)[0])
        cv2.imwrite(str(run / f"frames/{i:06d}.jpg"), np.zeros((H, W, 3), np.uint8))
        frames.append({
            "i": i, "image": f"frames/{i:06d}.jpg", "kept": True, "registered": True,
            "camera": {"width": W, "height": H, "model": "PINHOLE",
                       "params": [F, F, W / 2, H / 2]},
            "pose": {"qvec": qvec_from_matrix(R).tolist(), "tvec": t.tolist()},
            "center": c.tolist(),
        })  # fmt: skip
    pos.tofile(run / "web/pos.f32")
    np.cumsum([0] + [len(p) for p in fpts]).astype("<u4").tofile(run / "web/fo.u32")
    np.concatenate(fpts).astype("<u4").tofile(run / "web/fp.u32")
    man = {"version": 1, "world": {"bounds": {"min": [0, 0, 0], "max": [4, 4, 0]}},
           "points": {"count": len(pos), "position": "web/pos.f32"},
           "tracks": {"frame_offsets": "web/fo.u32", "frame_points": "web/fp.u32"},
           "frames": frames}  # fmt: skip
    (run / "web/manifest.json").write_text(json.dumps(man))
    # manual masks, simple naming: A in frames 0-2, B in frame 3
    md = tmp_path / "masks"
    md.mkdir()
    for i, reg in [(0, A), (1, A), (2, A), (3, B)]:
        R, t, _ = _pose(AZ[i])
        cv2.imwrite(str(md / f"{i:06d}_00_rust.png"), _quad_mask(R, t, reg))
    return {"run": run, "masks": md}


def test_confirmed_vs_low_evidence(scene: dict[str, Any]) -> None:
    run = scene["run"]
    prompts = [parse_prompt("rust:corrosion:watch")]
    m = segment_stage(run, ManualMasks(scene["masks"]), prompts, "registered", 1)
    assert m["observations"] == 4
    res = project_stage(run, voxel=0.1)
    assert res["findings"] == 2
    doc = json.loads((run / "findings/findings.json").read_text())
    a, b = sorted(doc["findings"], key=lambda f: -f["n_points"])
    assert (a["status"], a["frames"]) == ("confirmed", [0, 1, 2])
    assert a["max_angle_deg"] > 30
    assert (b["status"], b["frames"]) == ("low_evidence", [3])
    assert a["class"] == "corrosion" and a["severity"] == "watch"
    assert 0.9 < a["n_points"] / 441 < 1.01  # 21 x 21 grid points in region A
    # no scale: relative size only, never m2
    assert a["size"]["unit"] == "rel"
    assert a["size"]["value"] * (4 * 2**0.5) ** 2 == pytest.approx(1.0, rel=0.25)
    assert b["size"]["value"] * (4 * 2**0.5) ** 2 == pytest.approx(
        0.25, rel=0.5
    )  # + about one border cell
    assert 0 < a["support"] <= 1
    # point labels agree with the findings
    pf = np.fromfile(run / "findings/points.finding.u16", "<u2")
    assert (pf == a["id"]).sum() == a["n_points"] and (pf == b["id"]).sum() == b["n_points"]
    man = json.loads((run / "web/manifest.json").read_text())
    assert man["version"] == 2 and man["findings"] == "findings/findings.json"
    # re-run is idempotent
    assert project_stage(run, voxel=0.1)["findings"] == 2


def test_area_in_m2_with_scale(scene: dict[str, Any]) -> None:
    run = scene["run"]
    segment_stage(run, ManualMasks(scene["masks"]), [parse_prompt("rust")], "registered", 1)
    project_stage(run, scale=2.0, voxel=0.1)
    doc = json.loads((run / "findings/findings.json").read_text())
    a = max(doc["findings"], key=lambda f: f["n_points"])
    assert a["size"]["unit"] == "m2"
    assert a["size"]["value"] == pytest.approx(4.0, rel=0.25)  # 1 unit^2 * 2^2
    assert doc["scale"] == {"source": "reference", "m_per_unit": 2.0}


def test_no_masks_is_an_error(scene: dict[str, Any], tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    segment_stage(scene["run"], ManualMasks(empty), [parse_prompt("rust")], "registered", 1)
    with pytest.raises(StageError, match="found nothing"):
        project_stage(scene["run"])


def test_components_two_blobs() -> None:
    g = np.array([[x, y, 0.0] for x in range(3) for y in range(3)]) * 0.1
    lab = components(np.vstack([g, g + [5, 5, 0]]), 0.15)
    assert len(np.unique(lab)) == 2 and len(set(lab[:9])) == 1


# ---------------------------------------------------------------- Segmenter interface

IMG = np.zeros((30, 40, 3), np.uint8)
MASK = np.zeros((30, 40), np.uint8)
MASK[5:15, 10:30] = 255


def _manual(tmp_path: Path) -> ManualMasks:
    cv2.imwrite(str(tmp_path / "000007_00_rust.png"), MASK)
    return ManualMasks(tmp_path)


def _api(tmp_path: Path) -> SamApiSegmenter:
    ok, png = cv2.imencode(".png", MASK)
    assert ok
    canned = {"results": [{"prompt": "rust", "instances": [
        {"score": 0.9, "box": [10, 5, 30, 15],
         "mask": {"encoding": "png_base64", "data": base64.b64encode(png.tobytes()).decode()}},
        {"score": 0.5, "box": [0, 0, 0, 0], "mask": {"encoding": "rle", "size": [30, 40],
                                                     "counts": [1200]}},  # empty -> dropped
    ]}]}  # fmt: skip
    seen: list[dict[str, Any]] = []

    def transport(req: dict[str, Any]) -> dict[str, Any]:
        seen.append(req)
        assert req["prompts"] == [{"text": "rust"}] and req["image"]["format"] == "jpeg"
        return canned

    return SamApiSegmenter(transport)


@pytest.mark.parametrize("make", [_manual, _api])
def test_segmenter_interface(make: Any, tmp_path: Path) -> None:
    seg = make(tmp_path)
    assert {"name", "version", "licence", "dev_only"} <= set(seg.info())
    res = seg.segment(IMG, ["rust"], 7)
    assert len(res) == 1
    r = res[0]
    assert r.prompt == "rust" and r.mask.dtype == bool and r.mask.shape == (30, 40)
    assert r.mask.sum() == 200 and r.bbox == (10, 5, 30, 15)
    assert 0 <= r.confidence <= 1
    if make is _manual:
        assert seg.segment(IMG, ["crack"], 7) == []  # a prompt nobody drew


def test_sam_api_default_transport_is_a_clear_stub() -> None:
    with pytest.raises(NotImplementedError, match="META_MODEL_API_KEY"):
        SamApiSegmenter().segment(IMG, ["rust"], 0)


def test_sam_api_rle_roundtrip() -> None:
    m = np.zeros((30, 40), bool)
    m[2:6, 3:9] = True
    flat = m.T.reshape(-1)  # column-major
    change = np.flatnonzero(np.diff(np.concatenate([[False], flat, [False]])))
    counts = np.diff(np.concatenate([[0], change, [flat.size]])).tolist()
    out = SamApiSegmenter.decode_mask(
        {"encoding": "rle", "size": [30, 40], "counts": counts}, m.shape
    )
    assert np.array_equal(out, m)
