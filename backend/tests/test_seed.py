"""Dev seed demo site (owner, 2026-10-27): the tester's screenshots show a real finding from the
API, made by the same code as POST /api/inspections/{id}/findings, on the synthetic bundle of
test_findings_api.py standing in for the notre-dame-p3e run."""

from pathlib import Path

import pytest
from conftest import bearer
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_findings_api import write_bundle

from app import seed
from app.db import engine_for
from app.main import app
from app.models import AuditLog, Decision, Finding, Inspection, Run, Site
from app.settings import get_settings


@pytest.fixture
def dev_env(db_url: str, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("QARAU_ENV", "dev")
    for role in ("ADMIN", "ENGINEER", "VIEWER"):
        monkeypatch.setenv(f"QARAU_DEV_{role}_TOKEN", f"{role.lower()}-token")
    get_settings.cache_clear()
    return db_url


def count(db: Session, model: type) -> int:
    return db.scalar(select(func.count()).select_from(model)) or 0


def test_seed_adds_the_demo_site_once_with_a_real_api_finding(
    dev_env: str, runs_root: Path
) -> None:
    write_bundle(runs_root / seed.DEMO_RUN)
    seed.main()
    seed.main()  # idempotent: still one demo site
    with Session(engine_for(dev_env)) as db:
        (site,) = db.scalars(select(Site)).all()
        (insp,) = db.scalars(select(Inspection)).all()
        (run,) = db.scalars(select(Run)).all()
        assert site.name == "Demo site" and insp.site_id == site.id
        assert (run.id, run.run_dir, run.status) == ("notre-dame-p3e", "notre-dame-p3e", "done")
        assert run.inspection_id == insp.id
        assert count(db, Finding) == 1
        assert count(db, Decision) == 0  # the decision log is untouched
        (log,) = db.scalars(select(AuditLog)).all()
        assert (log.user_id, log.route, log.object_type, log.action) == (
            2,  # the engineer
            "python -m app.seed",
            "finding",
            "create",
        )
    r = TestClient(app).get(f"/api/inspections/{insp.id}/findings", headers=bearer("viewer"))
    assert r.status_code == 200, r.text
    (f,) = r.json()
    assert (f["run_id"], f["class"], f["severity"]) == ("notre-dame-p3e", "other", "watch")
    assert f["status"] == "proposed"
    assert f["comment"] == seed.DEMO_COMMENT and "not an inspected defect" in f["comment"]
    assert f["area_units"] > 0 and f["area_m2"] is None and f["scale"] == "not set"
    assert len(f["evidence"]) == 3
    for e in f["evidence"]:
        assert e["image_url"].startswith("/api/runs/notre-dame-p3e/files/frames/")
        assert len(e["outline"]) >= 3


def test_seed_without_the_run_adds_users_but_no_demo_site(
    dev_env: str, capsys: pytest.CaptureFixture[str]
) -> None:
    seed.main()
    assert "'Demo site' not added" in capsys.readouterr().out
    with Session(engine_for(dev_env)) as db:
        assert count(db, Site) == 0 and count(db, Finding) == 0


def test_demo_site_is_never_made_outside_dev(db_url: str, runs_root: Path) -> None:
    write_bundle(runs_root / seed.DEMO_RUN)
    settings = get_settings()
    assert settings.qarau_env != "dev"
    with Session(engine_for(db_url)) as db:
        org = seed.seed_dev_users(db, {})
        with pytest.raises(RuntimeError, match="QARAU_ENV"):
            seed.seed_demo(db, org, settings)
        assert count(db, Site) == 0
