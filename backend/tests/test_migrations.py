import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.db import engine_for
from app.models import (
    AuditLog,
    Decision,
    Finding,
    Inspection,
    Mode,
    Organization,
    Role,
    Run,
    Severity,
    Site,
    User,
    Verdict,
)

EXPECTED_TABLES = [
    "alembic_version",
    "audit_log",
    "decision",
    "finding",
    "inspection",
    "observation",
    "organization",
    "run",
    "scale_reference",
    "site",
    "user",
    "video_upload",
]

FORBIDDEN_SQL = [
    "UPDATE decision SET comment = 'edited'",
    "DELETE FROM decision",
    "UPDATE audit_log SET action = 'edited'",
    "DELETE FROM audit_log",
]


def test_upgrade_head_creates_exactly_these_tables(db_url: str) -> None:
    assert sorted(inspect(engine_for(db_url)).get_table_names()) == EXPECTED_TABLES


def seed_one_decision_and_audit_row(db_url: str) -> None:
    with Session(engine_for(db_url)) as db:
        org = Organization(name="o")
        db.add(org)
        db.flush()
        user = User(org_id=org.id, name="u", role=Role.engineer, secret_hash="h", salt="s")
        site = Site(org_id=org.id, name="s")
        db.add_all([user, site])
        db.flush()
        insp = Inspection(site_id=site.id, mode=Mode.quick_scan)
        db.add(insp)
        db.flush()
        run = Run(id="r" * 32, inspection_id=insp.id, run_dir="r" * 32)
        db.add(run)
        db.flush()
        finding = Finding(run_id=run.id, class_="corrosion", severity=Severity.watch, points=[])
        db.add(finding)
        db.flush()
        db.add(Decision(finding_id=finding.id, user_id=user.id, verdict=Verdict.confirm))
        db.add(AuditLog(user_id=user.id, route="POST /x", object_type="x", action="create"))
        db.commit()


@pytest.mark.parametrize("sql", FORBIDDEN_SQL)
def test_decision_and_audit_log_are_append_only(db_url: str, sql: str) -> None:
    seed_one_decision_and_audit_row(db_url)
    engine = engine_for(db_url)
    table = sql.split()[1] if sql.startswith("UPDATE") else sql.split()[2]
    with pytest.raises(DBAPIError, match="append-only"), engine.begin() as conn:
        conn.execute(text(sql))
    with engine.connect() as conn:
        count = conn.execute(text(f"SELECT count(*) FROM {table}")).scalar()  # noqa: S608
    assert count == 1
