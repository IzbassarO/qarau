"""Manual finding on real footage: a literal polygon over the chapel facade of the
notre-dame-p3e run (found once by a plane fit of the facade's points in frame 42). Skipped when
the run is absent. Renders the evidence frames with the outline (OpenCV) under
QARAU_OUT_DIR (default <repo>/data/out/def1-notre-dame, gitignored) for a visual check."""

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
OUT = Path(os.environ.get("QARAU_OUT_DIR", REPO / "data/out")) / "def1-notre-dame"
FACADE = [[-0.076, -0.192, 0.009], [-0.708, -0.718, 0.812], [-1.313, -0.948, 0.185],
          [-0.681, -0.423, -0.618]]  # fmt: skip

pytestmark = pytest.mark.skipif(
    not (RUN / "web/manifest.json").is_file(), reason=f"{RUN} is absent"
)


def test_facade_polygon_gives_three_frames_with_outlines_on_the_wall() -> None:
    bundle = load_bundle(RUN)
    sel = select_points(bundle.positions, FACADE)
    assert 300 <= len(sel) <= 800, len(sel)
    assert 0.5 < region_area(bundle.positions[sel]) < 1.5  # bundle units², no scale
    ev = evidence_for(bundle, sel)
    assert len(ev) == 3
    assert len({e["frame"] for e in ev}) == 3
    OUT.mkdir(parents=True, exist_ok=True)
    pt = np.repeat(np.arange(len(bundle.positions)), np.diff(bundle.offsets))
    for e in ev:
        cam = bundle.cameras[e["frame"]]
        outline = np.array(e["outline"])
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
        cv2.imwrite(str(OUT / f"frame_{e['frame']:06d}.png"), img)
