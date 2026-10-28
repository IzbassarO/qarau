"""GET /api/inspections/{id}/coverage (COV1) on fixed coverage_region rows, the regions.json
import (worker and idempotence), roles and the not-computed answer. Expected values are literals."""

import json
from pathlib import Path
from typing import Any

import pytest
from conftest import bearer
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import workflow
from app.coverage import NOT_COMPUTED, import_regions
from app.db import engine_for
from app.models import CoverageRegion, Inspection, Mode, Run

URL = "/api/inspections/1/coverage"
VIEW = bearer("viewer")
# 8 regions of run "r-b": 5 inspected_ok, 1 finding, 2 not_seen -> 62.5 / 12.5 / 25.0 %
ROWS = [
    ("0_0_0", "inspected_ok"), ("1_0_0", "inspected_ok"), ("2_0_0", "inspected_ok"),
    ("0_1_0", "inspected_ok"), ("1_1_0", "inspected_ok"), ("2_1_0", "finding"),
    ("-1_0_0", "not_seen"), ("3_0_0", "not_seen"),
]  # fmt: skip
TOTALS = {
    "inspected_ok": {"count": 5, "percent": 62.5},
    "finding": {"count": 1, "percent": 12.5},
    "not_seen": {"count": 2, "percent": 25.0},
}
REGIONS_JSON = {
    "schema_version": 1,
    "voxel_size": 0.5,
    "regions": [
        {"id": "0_0_0", "bounds": [[0, 0, 0], [0.5, 0.5, 0.5]], "state": "inspected_ok",
         "frame_count": 4, "point_count": 30, "nearest_frames": ["000001.jpg", "000002.jpg"]},
        {"id": "-1_0_0", "bounds": [[-0.5, 0, 0], [0, 0.5, 0.5]], "state": "not_seen",
         "frame_count": 1, "point_count": 8, "nearest_frames": ["000009.jpg"]},
    ],
}  # fmt: skip


def add_runs(db_url: str, *run_ids: str) -> None:
    with Session(engine_for(db_url)) as db:
        for r in run_ids:
            db.add(Run(id=r, inspection_id=1, run_dir=r, status="done"))
        db.commit()


def add_rows(db_url: str, run_id: str, rows: list[tuple[str, str]]) -> None:
    with Session(engine_for(db_url)) as db:
        for i, (key, state) in enumerate(rows):
            db.add(CoverageRegion(run_id=run_id, region_key=key, state=state,
                                  bounds=[[i, 0, 0], [i + 0.5, 0.5, 0.5]], frame_count=i,
                                  point_count=10 * i, nearest_frames=[f"{i:06d}.jpg"]))  # fmt: skip
        db.commit()


@pytest.fixture
def covered(client: TestClient, db_url: str) -> TestClient:
    """Inspection 1 with runs r-a (older coverage, all not_seen) and r-b (the ROWS)."""
    add_runs(db_url, "r-a", "r-b")
    add_rows(db_url, "r-a", [("0_0_0", "not_seen")])
    add_rows(db_url, "r-b", ROWS)
    return client


def test_coverage_totals_and_regions_of_the_latest_run(covered: TestClient) -> None:
    r = covered.get(URL, headers=VIEW)
    assert r.status_code == 200, r.text
    body: dict[str, Any] = r.json()
    assert (body["inspection_id"], body["status"], body["message"], body["run_id"]) == (
        1, "computed", None, "r-b"
    )  # fmt: skip
    assert body["totals"] == TOTALS
    assert len(body["regions"]) == 8
    assert body["regions"][0] == {"id": "0_0_0", "state": "inspected_ok",
                                  "bounds": [[0, 0, 0], [0.5, 0.5, 0.5]], "frame_count": 0,
                                  "point_count": 0, "nearest_frames": ["000000.jpg"]}  # fmt: skip
    assert body["regions"][7] == {"id": "3_0_0", "state": "not_seen",
                                  "bounds": [[7, 0, 0], [7.5, 0.5, 0.5]], "frame_count": 7,
                                  "point_count": 70, "nearest_frames": ["000007.jpg"]}  # fmt: skip


def test_not_computed_says_so_and_lists_nothing(client: TestClient) -> None:
    body = client.get(URL, headers=VIEW).json()
    assert body == {"inspection_id": 1, "status": "not_computed", "run_id": None, "totals": None,
                    "regions": [], "message": "Coverage has not been computed for this inspection "
                    "yet, so no part of the map is known to be inspected. Treat the whole map as "
                    "not inspected."}  # fmt: skip
    assert body["message"] == NOT_COMPUTED


def test_unknown_inspection_is_404(client: TestClient) -> None:
    r = client.get("/api/inspections/99/coverage", headers=VIEW)
    assert r.status_code == 404
    assert r.json() == {"detail": "inspection not found"}


def test_login_required_and_every_role_may_read(covered: TestClient) -> None:
    assert covered.get(URL).status_code == 401
    assert covered.get(URL, headers=bearer("not-a-token")).status_code == 401
    for role in ("viewer", "engineer", "admin"):
        assert covered.get(URL, headers=bearer(role)).status_code == 200, role


def test_other_inspection_does_not_see_these_rows(covered: TestClient, db_url: str) -> None:
    with Session(engine_for(db_url)) as db:
        db.add(Inspection(site_id=1, mode=Mode.quick_scan))
        db.commit()
    assert covered.get("/api/inspections/2/coverage", headers=VIEW).json()["status"] == (
        "not_computed"
    )


def test_import_regions_is_idempotent(client: TestClient, db_url: str, tmp_path: Path) -> None:
    add_runs(db_url, "r-x")
    (tmp_path / "coverage").mkdir()
    (tmp_path / "coverage" / "regions.json").write_text(json.dumps(REGIONS_JSON))
    for _ in range(2):
        with Session(engine_for(db_url)) as db:
            import_regions(db, "r-x", tmp_path)
            db.commit()
    body = client.get(URL, headers=VIEW).json()
    assert body["totals"] == {"inspected_ok": {"count": 1, "percent": 50.0},
                              "finding": {"count": 0, "percent": 0.0},
                              "not_seen": {"count": 1, "percent": 50.0}}  # fmt: skip
    assert [(g["id"], g["state"], g["point_count"], g["nearest_frames"]) for g in body["regions"]] \
        == [("0_0_0", "inspected_ok", 30, ["000001.jpg", "000002.jpg"]),
            ("-1_0_0", "not_seen", 8, ["000009.jpg"])]  # fmt: skip


class CoverageRunner:
    """Fake step runner whose coverage step writes regions.json, as the pipeline does."""

    def __init__(self, doc: Any) -> None:
        self.doc = doc

    def names(self) -> list[str]:
        return ["export", "coverage"]

    def run(self, run_dir: Path, name: str, force: bool, prefix: str) -> dict[str, Any]:
        if name == "coverage":
            (run_dir / "coverage").mkdir(parents=True, exist_ok=True)
            (run_dir / "coverage" / "regions.json").write_text(json.dumps(self.doc))
        return {"name": name, "status": "done", "started_at": "2026-10-27T10:00:00+00:00",
                "finished_at": "2026-10-27T10:00:01+00:00", "error": None}  # fmt: skip


def count_rows(db_url: str, run_id: str) -> int:
    with Session(engine_for(db_url)) as db:
        n = db.scalar(select(func.count()).where(CoverageRegion.run_id == run_id))
    return int(n or 0)


def test_worker_imports_coverage_after_the_step(
    client: TestClient, db_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    add_runs(db_url, "r-w")
    monkeypatch.setattr(workflow, "make_runner", lambda: CoverageRunner(REGIONS_JSON))
    assert workflow.run_inspection("r-w") == "done"
    assert workflow.run_inspection("r-w", "coverage") == "done"  # a rerun replaces the rows
    assert count_rows(db_url, "r-w") == 2
    assert client.get(URL, headers=VIEW).json()["run_id"] == "r-w"


def test_unreadable_regions_fail_the_coverage_step(
    client: TestClient, db_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    add_runs(db_url, "r-bad")
    monkeypatch.setattr(workflow, "make_runner", lambda: CoverageRunner({"regions": [{}]}))
    assert workflow.run_inspection("r-bad") == "failed"
    with Session(engine_for(db_url)) as db:
        run = db.get(Run, "r-bad")
        assert run is not None
        assert run.error == "coverage: coverage import: KeyError: 'id'"
        assert [(s["name"], s["status"]) for s in run.steps] == [("export", "done"),
                                                                ("coverage", "failed")]  # fmt: skip
    assert count_rows(db_url, "r-bad") == 0
