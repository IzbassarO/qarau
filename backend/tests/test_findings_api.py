"""Manual findings API on a fixture bundle in tmp_path (the same synthetic scene as
pipeline/tests/test_findings.py: a 3 x 2 unit grid on z = 0, points 0..11; two off-plane points
12, 13; one in-plane point 14 outside the polygon; 4 cameras, hand-written frame links)."""

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from conftest import SHARED_TOKEN, bearer
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db import engine_for
from app.models import Run, ScaleReference

GRID = [[x, y, 0.0] for y in range(3) for x in range(4)]
POINTS = GRID + [[1.5, 1.0, 1.0], [1.5, 1.0, -1.0], [5.0, 1.0, 0.0]]
POLYGON = [[-0.5, -0.5, 0], [3.5, -0.5, 0], [3.5, 2.5, 0], [-0.5, 2.5, 0]]
CAMS = [
    ([0.0, 1.0, 0.0, 0.0], [-1.5, 1.0, 10.0]),
    ([0.0, 1.0, 0.0, 0.0], [-1.5, 1.0, 10.5]),
    ([0.0, 0.9238795325, 0.0, -0.3826834324], [-1.0606601718, 1.0, 12.3743686708]),
    ([0.3826834324, -0.9238795325, 0.0, 0.0], [-1.5, 0.7071067812, 12.0208152802]),
]
LINKS = [[0, 1], [0, 1, 2], [0, 1, 2], [0, 1, 2], [0, 1, 3], [0, 1, 2, 3], [0, 1, 2, 3],
         [0, 1, 2, 3], [0, 1, 3], [0, 1, 2, 3], [0, 1, 2], [0, 1], [1], [1], [1, 2]]  # fmt: skip
CAMERA = {"width": 100, "height": 100, "model": "PINHOLE", "params": [100, 100, 50, 50]}
RUN_ID = "f" * 32
ENG, VIEW = bearer("engineer"), bearer("viewer")
BODY: dict[str, Any] = {"class": "corrosion", "severity": "watch", "polygon": POLYGON,
                        "comment": "paint gone on the lower band"}  # fmt: skip


def write_bundle(run: Path, prefix: str = "") -> None:
    """Bundle under <run>/web; `prefix` ("export/") puts the array paths of the manifest under
    the S1 layout, where `run` is the <run>/export dir and paths are relative to the run root."""
    (run / "web").mkdir(parents=True)
    np.array(POINTS, "<f4").tofile(run / "web/points.position.f32")
    np.cumsum([0] + [len(r) for r in LINKS]).astype("<u4").tofile(run / "web/po.u32")
    np.array([f for r in LINKS for f in r], "<u2").tofile(run / "web/pf.u16")
    frames = [{"i": i, "image": f"frames/{i:06d}.jpg", "registered": True, "camera": CAMERA,
               "pose": {"qvec": q, "tvec": t}} for i, (q, t) in enumerate(CAMS)]  # fmt: skip
    man = {"version": 2, "frames": frames,
           "points": {"count": len(POINTS), "position": f"{prefix}web/points.position.f32"},
           "tracks": {"point_offsets": f"{prefix}web/po.u32",
                      "point_frames": f"{prefix}web/pf.u16"}}  # fmt: skip
    (run / "web/manifest.json").write_text(json.dumps(man))


@pytest.fixture
def marked(client: TestClient, db_url: str, runs_root: Path) -> TestClient:
    """Inspection 1 has an exported run `fixture-run` with the synthetic bundle."""
    write_bundle(runs_root / "fixture-run")
    with Session(engine_for(db_url)) as db:
        db.add(Run(id=RUN_ID, inspection_id=1, run_dir="fixture-run", status="exported"))
        db.commit()
    return client


def add_scale(db_url: str, m_per_unit: float) -> None:
    with Session(engine_for(db_url)) as db:
        db.add(ScaleReference(inspection_id=1, kind="manual", payload={"m_per_unit": m_per_unit},
                              source="door height 2.0 m"))  # fmt: skip
        db.commit()


def test_post_polygon_without_scale(marked: TestClient) -> None:
    r = marked.post("/api/inspections/1/findings", json=BODY, headers=ENG)
    assert r.status_code == 201, r.text
    f = r.json()
    assert (f["id"], f["inspection_id"], f["run_id"]) == (1, 1, RUN_ID)
    assert (f["class"], f["severity"], f["status"]) == ("corrosion", "watch", "proposed")
    assert f["comment"] == "paint gone on the lower band"
    assert f["area_units"] == pytest.approx(6.0, abs=1e-6)
    assert f["area_m2"] is None and f["scale"] == "not set"
    # never a metre value without a scale: no field with m2 carries a number
    assert [k for k, v in f.items() if "m2" in k and v is not None] == []
    assert f["point_indices"] == list(range(12))
    assert f["points"][0] == [0.0, 0.0, 0.0] and f["points"][11] == [3.0, 2.0, 0.0]
    ev = f["evidence"]
    assert [e["frame_id"] for e in ev] == [0, 2, 3]
    assert [e["confidence"] for e in ev] == [1.0, 1.0, 1.0]
    assert [e["image_url"] for e in ev] == [
        f"/api/runs/fixture-run/files/frames/{i:06d}.jpg" for i in (0, 2, 3)
    ]
    np.testing.assert_allclose(ev[0]["outline"], [[35, 40], [65, 40], [65, 60], [35, 60]], atol=0.5)
    for e in ev:
        assert len(e["outline"]) >= 3
        assert all(0 <= u <= 100 and 0 <= v <= 100 for u, v in e["outline"])
    log = marked.get("/api/audit-log", headers=bearer("admin")).json()
    assert [(x["route"], x["object_type"], x["object_id"], x["action"]) for x in log] == [
        ("POST /api/inspections/1/findings", "finding", "1", "create")
    ]


def test_post_with_fake_scale_reference_gives_m2(marked: TestClient, db_url: str) -> None:
    add_scale(db_url, 0.5)
    r = marked.post("/api/inspections/1/findings", json=BODY, headers=ENG)
    assert r.status_code == 201, r.text
    f = r.json()
    assert f["area_units"] == pytest.approx(6.0, abs=1e-6)
    assert f["area_m2"] == pytest.approx(1.5, abs=1e-6)  # 6 units² x (0.5 m/unit)²
    assert f["scale"] == "manual: door height 2.0 m"


def test_scale_2m_per_unit_turns_a_unit_square_into_4_m2(marked: TestClient, db_url: str) -> None:
    add_scale(db_url, 2.0)
    body = {"class": "coating", "severity": "watch", "point_indices": [0, 1, 4, 5]}  # unit square
    r = marked.post("/api/inspections/1/findings", json=body, headers=ENG)
    assert r.status_code == 201, r.text
    f = r.json()
    assert f["area_units"] == pytest.approx(1.0, abs=1e-6)
    assert f["area_m2"] == pytest.approx(4.0, abs=1e-6)  # 1 unit² x (2.0 m/unit)²
    assert f["scale"] == "manual: door height 2.0 m"


def test_post_point_indices(marked: TestClient) -> None:
    body = {"class": "crack", "severity": "critical", "point_indices": [5, 0, 1, 4]}
    r = marked.post("/api/inspections/1/findings", json=body, headers=ENG)
    assert r.status_code == 201, r.text
    f = r.json()
    assert f["point_indices"] == [0, 1, 4, 5] and f["area_units"] == pytest.approx(1.0)
    assert f["class"] == "crack" and f["severity"] == "critical" and f["comment"] is None
    assert len(f["evidence"]) == 3 and f["evidence"][0]["frame_id"] == 0


@pytest.mark.parametrize(
    "bad",
    [
        {**BODY, "polygon": POLYGON[:2]},  # 2 vertices
        {**BODY, "class": "rust"},  # unknown class
        {**BODY, "severity": "medium"},
        {**BODY, "point_indices": [0]},  # both regions
        {k: v for k, v in BODY.items() if k != "polygon"},  # no region
        {**BODY, "polygon": [[0, 0, 0], [1, 0, 0], [2, 0, 0]]},  # collinear
        {**BODY, "polygon": [[50, 50, 0], [51, 50, 0], [51, 51, 0]]},  # selects no point
        {"class": "crack", "severity": "watch", "point_indices": [99]},  # out of range
        {"class": "crack", "severity": "watch", "point_indices": [1, 1]},  # duplicates
        {"class": "crack", "severity": "watch", "point_indices": []},
    ],
)
def test_bad_bodies_are_422(marked: TestClient, bad: dict[str, Any]) -> None:
    r = marked.post("/api/inspections/1/findings", json=bad, headers=ENG)
    assert r.status_code == 422, r.text
    assert marked.get("/api/inspections/1/findings", headers=VIEW).json() == []


def test_roles(marked: TestClient) -> None:
    assert marked.post("/api/inspections/1/findings", json=BODY, headers=VIEW).status_code == 403
    assert marked.post("/api/inspections/1/findings", json=BODY).status_code == 401
    r = marked.post("/api/inspections/1/findings", json=BODY, headers=bearer(SHARED_TOKEN))
    assert r.status_code == 403
    assert marked.get("/api/inspections/1/findings", headers=VIEW).status_code == 200
    assert marked.get("/api/inspections/1/findings").status_code == 401


def test_inspection_without_exported_run_is_409(client: TestClient) -> None:
    r = client.post("/api/inspections/1/findings", json=BODY, headers=ENG)
    assert r.status_code == 409
    assert "no exported run" in r.json()["detail"]
    assert client.post("/api/inspections/99/findings", json=BODY, headers=ENG).status_code == 404


def test_new_run_layout_export_web(client: TestClient, db_url: str, runs_root: Path) -> None:
    """S1 runs (ADR 0022) keep the bundle under <run>/export/web; the manifest paths stay
    relative to the run root, so the image URLs still point at /files/frames/…"""
    write_bundle(runs_root / "new-run" / "export", prefix="export/")
    with Session(engine_for(db_url)) as db:
        db.add(Run(id="e" * 32, inspection_id=1, run_dir="new-run", status="done"))
        db.commit()
    r = client.post("/api/inspections/1/findings", json=BODY, headers=ENG)
    assert r.status_code == 201, r.text
    f = r.json()
    assert f["run_id"] == "e" * 32 and f["point_indices"] == list(range(12))
    assert f["evidence"][0]["image_url"] == "/api/runs/new-run/files/frames/000000.jpg"


def test_get_routes_return_the_stored_finding(marked: TestClient) -> None:
    created = marked.post("/api/inspections/1/findings", json=BODY, headers=ENG).json()
    listed = marked.get("/api/inspections/1/findings", headers=VIEW).json()
    assert listed == [created]
    assert marked.get("/api/findings/1", headers=VIEW).json() == created
    assert marked.get("/api/findings/2", headers=VIEW).status_code == 404
    assert marked.get("/api/inspections/99/findings", headers=VIEW).status_code == 404
    assert marked.get("/api/inspections/1/findings", headers=bearer(SHARED_TOKEN)).json() == listed
