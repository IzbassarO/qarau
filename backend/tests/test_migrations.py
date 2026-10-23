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
    "run_step",
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


RUN_COLUMNS = ["created_at", "current_step", "error", "id", "inspection_id", "progress", "run_dir",
               "status"]  # fmt: skip
RUN_STEP_COLUMNS = ["duration_s", "error", "finished_at", "id", "name", "run_id", "started_at",
                    "status"]  # fmt: skip


def test_upgrade_head_creates_exactly_these_tables(db_url: str) -> None:
    insp = inspect(engine_for(db_url))
    assert sorted(insp.get_table_names()) == EXPECTED_TABLES
    assert sorted(c["name"] for c in insp.get_columns("run")) == RUN_COLUMNS
    assert sorted(c["name"] for c in insp.get_columns("run_step")) == RUN_STEP_COLUMNS
    assert [u["column_names"] for u in insp.get_unique_constraints("run_step")] == [
        ["run_id", "name"]
    ]


def test_v0002_upgrades_an_existing_run_and_downgrades(db_url: str) -> None:
    """A run written under 0001 gets created_at from the server default; downgrade removes
    the new columns and table again."""
    from conftest import alembic_config, drop_everything

    from alembic import command

    engine = engine_for(db_url)
    drop_everything(db_url)
    command.upgrade(alembic_config(db_url), "0001")
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO organization (id, name) VALUES (1, 'o')"))
        conn.execute(text("INSERT INTO site (id, org_id, name) VALUES (1, 1, 's')"))
        conn.execute(
            text(
                "INSERT INTO inspection (id, site_id, mode, status, created_at) "
                "VALUES (1, 1, 'quick_scan', 'created', CURRENT_TIMESTAMP)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO run (id, inspection_id, run_dir, status, progress) "
                "VALUES ('r1', 1, 'r1', 'uploaded', 0)"
            )
        )
    command.upgrade(alembic_config(db_url), "head")
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT status, current_step, error, created_at FROM run WHERE id = 'r1'")
        ).one()
        assert (row[0], row[1], row[2]) == ("uploaded", None, None) and row[3] is not None
        assert conn.execute(text("SELECT count(*) FROM run_step")).scalar() == 0
    command.downgrade(alembic_config(db_url), "0001")
    insp = inspect(engine)
    assert "run_step" not in insp.get_table_names()
    assert sorted(c["name"] for c in insp.get_columns("run")) == [
        "id", "inspection_id", "progress", "run_dir", "status"
    ]  # fmt: skip


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
