"""Coverage regions (COV1-b): the workflow fill from <run>/coverage/regions.json (idempotent, a
missing file leaves no rows) and GET /api/inspections/{id}/coverage. Expected values are literals
from tests/fixtures/coverage/regions.json: 2 inspected_ok, 1 finding, 1 not_seen."""

import shutil
from pathlib import Path
from typing import Any

import pytest
from conftest import SHARED_TOKEN, bearer
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import workflow
from app.db import engine_for
from app.models import CoverageRegion, Inspection, Mode, Organization, Run, Site
from app.settings import get_settings

FIXTURE = Path(__file__).parent / "fixtures" / "coverage" / "regions.json"
RUN = "c" * 32
FIXTURE_REGIONS = [
    {"id": "-1_0_0", "state": "inspected_ok", "frame_count": 7,
     "bounds": {"min": [-1.0, 0.0, 0.0], "max": [0.0, 1.0, 1.0]}},
    {"id": "0_0_0", "state": "inspected_ok", "frame_count": 5,
     "bounds": {"min": [0.0, 0.0, 0.0], "max": [1.0, 1.0, 1.0]}},
    {"id": "0_0_1", "state": "finding", "frame_count": 4,
     "bounds": {"min": [0.0, 0.0, 1.0], "max": [1.0, 1.0, 2.0]}},
    {"id": "2_-1_0", "state": "not_seen", "frame_count": 1,
     "bounds": {"min": [2.0, -1.0, 0.0], "max": [3.0, 0.0, 1.0]}},
]  # fmt: skip


def add_run(run_id: str = RUN, inspection_id: int = 1) -> Path:
    with Session(engine_for(get_settings().database_url)) as db:
        db.add(Run(id=run_id, inspection_id=inspection_id, run_dir=run_id))
        db.commit()
    return Path(get_settings().qarau_runs_dir) / run_id


def with_regions(run_dir: Path) -> Path:
    (run_dir / "coverage").mkdir(parents=True)
    shutil.copyfile(FIXTURE, run_dir / "coverage" / "regions.json")
    return run_dir


def stored_states(run_id: str = RUN) -> list[str]:
    with Session(engine_for(get_settings().database_url)) as db:
        rows = db.scalars(select(CoverageRegion).where(CoverageRegion.run_id == run_id))
        return sorted(r.state.value for r in rows)


FOUR = ["finding", "inspected_ok", "inspected_ok", "not_seen"]


def test_fill_twice_keeps_exactly_the_four_fixture_regions(client: TestClient) -> None:
    run_dir = with_regions(add_run())
    assert workflow.fill_coverage(RUN, run_dir) == 4
    assert workflow.fill_coverage(RUN, run_dir) == 4
    assert stored_states() == FOUR


def test_missing_regions_file_leaves_no_rows_and_warns(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    run_dir = with_regions(add_run())
    workflow.fill_coverage(RUN, run_dir)
    (run_dir / "coverage" / "regions.json").unlink()  # coverage of a rerun went missing
    assert workflow.fill_coverage(RUN, run_dir) == 0
    assert stored_states() == []
    assert "no coverage regions stored" in caplog.text


class CoverageRunner:
    """The coverage step alone; it 'writes' regions.json by copying the fixture."""

    def names(self) -> list[str]:
        return ["coverage"]

    def run(self, run_dir: Path, name: str, force: bool, prefix: str) -> dict[str, Any]:
        if not (run_dir / "coverage").exists():
            with_regions(run_dir)
        return {"name": name, "status": "done", "started_at": "2026-10-28T10:00:00+00:00",
                "finished_at": "2026-10-28T10:00:01+00:00", "duration_s": 1.0,
                "error": None}  # fmt: skip


def test_the_job_fills_the_rows_after_the_coverage_step(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    add_run()
    monkeypatch.setattr(workflow, "make_runner", CoverageRunner)
    assert workflow.run_inspection(RUN) == "done"
    assert workflow.run_inspection(RUN, "coverage") == "done"  # rerun: no duplicates
    assert stored_states() == FOUR


def test_viewer_gets_the_fixture_regions_and_totals(client: TestClient) -> None:
    workflow.fill_coverage(RUN, with_regions(add_run()))
    r = client.get("/api/inspections/1/coverage", headers=bearer("viewer"))
    assert r.status_code == 200
    assert r.json() == {
        "regions": FIXTURE_REGIONS,
        "totals": {"inspected_ok": 50.0, "finding": 25.0, "not_seen": 25.0},
        "computed": True,
    }


def test_no_token_is_401(client: TestClient) -> None:
    workflow.fill_coverage(RUN, with_regions(add_run()))
    assert client.get("/api/inspections/1/coverage").status_code == 401


def test_not_computed_says_so_with_empty_totals(client: TestClient) -> None:
    add_run()  # a run without coverage
    r = client.get("/api/inspections/1/coverage", headers=bearer("viewer"))
    assert r.json() == {"regions": [], "totals": {}, "computed": False}


def test_unknown_inspection_and_other_organization_are_404(client: TestClient) -> None:
    with Session(engine_for(get_settings().database_url)) as db:
        org = Organization(name="other")
        db.add(org)
        db.flush()
        site = Site(org_id=org.id, name="Other site")
        db.add(site)
        db.flush()
        insp = Inspection(site_id=site.id, mode=Mode.quick_scan)
        db.add(insp)
        db.commit()
        other = f"/api/inspections/{insp.id}/coverage"
    workflow.fill_coverage("d" * 32, with_regions(add_run("d" * 32, inspection_id=insp.id)))
    assert client.get("/api/inspections/99/coverage", headers=bearer("viewer")).status_code == 404
    assert client.get(other, headers=bearer("viewer")).status_code == 404
    # The shared read-only dev token has no organization: it reads, as on every route.
    shared = {"Authorization": f"Bearer {SHARED_TOKEN}"}
    assert client.get(other, headers=shared).json()["computed"] is True
