"""Manual finding on real footage: a literal polygon over the chapel facade of the
notre-dame-p3e run (found once by a plane fit of the facade's points in frame 42). Skipped when
the run is absent. Renders the evidence frames with the outline (OpenCV) into the test's tmp_path,
or under QARAU_OUT_DIR when set, for a visual check. Never into the repo: Kerege points
QARAU_RUNS_DIR at the real runs, and the 3 frames landed in the worktree's data/ (reverted as
forbidden files in every round of D1, REV1-api, RPT1)."""

from __future__ import annotations

import os
from pathlib import Path

import cv2
import numpy as np
import pytest

from qarau_pipeline.cameras import project
from qarau_pipeline.findings import evidence_for, load_bundle, region_area, select_points

REPO = Path(__file__).resolve().parents[2]
RUN = Path(os.environ.get("QARAU_RUNS_DIR", REPO / "data/runs")) / "notre-dame-p3e"
FACADE = [[-0.076, -0.192, 0.009], [-0.708, -0.718, 0.812], [-1.313, -0.948, 0.185],
          [-0.681, -0.423, -0.618]]  # fmt: skip

MANIFEST = RUN / "web/manifest.json"
NO_RUN = pytest.mark.skipif(not MANIFEST.is_file(), reason="notre-dame-p3e run is absent")


def out_dir(tmp_path: Path) -> Path:
    return Path(os.environ.get("QARAU_OUT_DIR") or tmp_path) / "notre-dame-p3e"


def test_frames_go_to_tmp_path_unless_qarau_out_dir_is_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("QARAU_OUT_DIR", raising=False)
    assert out_dir(tmp_path) == tmp_path / "notre-dame-p3e"
    monkeypatch.setenv("QARAU_OUT_DIR", "/tmp/check")
    assert out_dir(tmp_path) == Path("/tmp/check/notre-dame-p3e")


@NO_RUN
def test_facade_polygon_gives_three_frames_with_outlines_on_the_wall(tmp_path: Path) -> None:
    bundle = load_bundle(RUN)
    sel = select_points(bundle.positions, FACADE)
    assert 300 <= len(sel) <= 800, len(sel)
    assert 0.5 < region_area(bundle.positions[sel]) < 1.5  # bundle units², no scale
    ev = evidence_for(bundle, sel)
    assert len(ev) == 3
    assert len({e["frame"] for e in ev}) == 3
    out = out_dir(tmp_path)
    out.mkdir(parents=True, exist_ok=True)
    pt = np.repeat(np.arange(len(bundle.positions)), np.diff(bundle.offsets))
    for e in ev:
        cam = bundle.cameras[e["frame"]]
        outline = np.array(e["outline"])
        # >= 3 outline vertices, every one inside the image
        assert len(outline) >= 3
        assert (outline[:, 0] >= 0).all() and (outline[:, 0] <= cam.width).all()
        assert (outline[:, 1] >= 0).all() and (outline[:, 1] <= cam.height).all()
        # >= 80 % of the selected points this frame sees project inside its outline
        seen = np.intersect1d(sel, pt[bundle.frame_ids == e["frame"]])
        assert len(seen) >= 10  # sparse tracks: a point is tracked in ~7 of 121 frames
        xc = bundle.positions[seen] @ cam.R.T + cam.t
        px = project(cam.model, cam.params, xc[xc[:, 2] > 0])
        poly = outline.astype(np.float32)
        inside = [cv2.pointPolygonTest(poly, (float(u), float(v)), False) >= 0 for u, v in px]
        assert np.mean(inside) >= 0.8, (e["frame"], np.mean(inside))
        img = cv2.imread(str(RUN / e["image"]))
        assert img is not None
        cv2.polylines(img, [outline.round().astype(np.int32).reshape(-1, 1, 2)], True,
                      (0, 0, 255), 2)  # fmt: skip
        cv2.imwrite(str(out / f"frame_{e['frame']:06d}.png"), img)
