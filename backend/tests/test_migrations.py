import pytest
import sqlalchemy as sa
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
    "report",
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


RUN_COLUMNS = ["created_at", "current_step", "error", "id", "inspection_id", "progress",
               "rq_job_id", "run_dir", "status", "steps"]  # fmt: skip


def test_upgrade_head_creates_exactly_these_tables(db_url: str) -> None:
    insp = inspect(engine_for(db_url))
    assert sorted(insp.get_table_names()) == EXPECTED_TABLES
    cols = {c["name"]: c for c in insp.get_columns("run")}
    assert sorted(cols) == RUN_COLUMNS
    assert isinstance(cols["progress"]["type"], sa.Integer)
    assert not cols["steps"]["nullable"]


def test_v0002_upgrades_an_existing_run_and_downgrades(db_url: str) -> None:
    """A run written under 0001 gets created_at from the server default, an empty step list and
    an integer progress; downgrade removes the new columns again."""
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
                "VALUES ('r1', 1, 'r1', 'uploaded', 0.0)"
            )
        )
    command.upgrade(alembic_config(db_url), "head")
    with Session(engine) as db:
        run = db.get(Run, "r1")
        assert run is not None
        assert (run.status, run.progress, run.current_step, run.error, run.rq_job_id) == (
            "uploaded", 0, None, None, None
        )  # fmt: skip
        assert run.steps == [] and run.created_at is not None
    command.downgrade(alembic_config(db_url), "0001")
    insp = inspect(engine)
    cols = {c["name"]: c for c in insp.get_columns("run")}
    assert sorted(cols) == ["id", "inspection_id", "progress", "run_dir", "status"]
    assert isinstance(cols["progress"]["type"], sa.Float)


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


DECISION_COLUMNS_0003 = ["comment", "created_at", "finding_id", "id", "user_id", "verdict"]
DECISION_COLUMNS_0004 = sorted([*DECISION_COLUMNS_0003, "new_class", "new_severity"])


def test_v0004_adds_reclass_columns_and_keeps_the_append_only_trigger(db_url: str) -> None:
    """0004 adds `new_class` / `new_severity` to `decision` with plain ADD COLUMN; the 0001
    trigger survives the upgrade and the downgrade (batch mode would have recreated the table)."""
    from conftest import alembic_config

    from alembic import command

    engine = engine_for(db_url)
    cols = {c["name"]: c for c in inspect(engine).get_columns("decision")}
    assert sorted(cols) == DECISION_COLUMNS_0004
    assert cols["new_class"]["nullable"] and cols["new_severity"]["nullable"]
    seed_one_decision_and_audit_row(db_url)
    with pytest.raises(DBAPIError, match="append-only"), engine.begin() as conn:
        conn.execute(text("UPDATE decision SET new_class = 'crack'"))

    command.downgrade(alembic_config(db_url), "0003")
    assert (
        sorted(c["name"] for c in inspect(engine).get_columns("decision")) == DECISION_COLUMNS_0003
    )
    with pytest.raises(DBAPIError, match="append-only"), engine.begin() as conn:
        conn.execute(text("DELETE FROM decision"))

    command.upgrade(alembic_config(db_url), "head")
    assert (
        sorted(c["name"] for c in inspect(engine).get_columns("decision")) == DECISION_COLUMNS_0004
    )
    with Session(engine) as db:
        d = db.query(Decision).one()  # the 0003 row survived with null new_* columns
        assert (d.verdict, d.comment, d.new_class, d.new_severity) == (
            Verdict.confirm,
            None,
            None,
            None,
        )
    with pytest.raises(DBAPIError, match="append-only"), engine.begin() as conn:
        conn.execute(text("UPDATE decision SET comment = 'edited'"))


REPORT_COLUMNS = ["created_by", "generated_at", "id", "inspection_id", "lang", "report_number",
                  "sha256", "version"]  # fmt: skip


def test_v0005_creates_the_report_table_and_downgrades(db_url: str) -> None:
    """0005 adds `report` (one row per generated PDF) with foreign keys to inspection and user;
    downgrade to 0004 drops it and leaves the other tables, upgrade brings it back."""
    from conftest import alembic_config

    from alembic import command

    engine = engine_for(db_url)
    insp = inspect(engine)
    cols = {c["name"]: c for c in insp.get_columns("report")}
    assert sorted(cols) == REPORT_COLUMNS
    assert cols["created_by"]["nullable"]
    assert not any(cols[c]["nullable"] for c in REPORT_COLUMNS if c != "created_by")
    assert getattr(cols["sha256"]["type"], "length", None) == 64
    fks = {
        fk["constrained_columns"][0]: fk["referred_table"] for fk in insp.get_foreign_keys("report")
    }
    assert fks == {"inspection_id": "inspection", "created_by": "user"}
    seed_one_decision_and_audit_row(db_url)
    row = ("INSERT INTO report (inspection_id, lang, sha256, generated_at, report_number, "
           "version, created_by) VALUES (1, 'en', :sha, CURRENT_TIMESTAMP, 'QV-2026-0001', 1, "
           "NULL)")  # fmt: skip
    with engine.begin() as conn:
        conn.execute(text(row), {"sha": "a" * 64})
    with pytest.raises(DBAPIError), engine.begin() as conn:  # no such inspection
        conn.execute(text(row.replace("(1, 'en'", "(99, 'en'")), {"sha": "b" * 64})

    command.downgrade(alembic_config(db_url), "0004")
    tables = sorted(inspect(engine).get_table_names())
    assert tables == [t for t in EXPECTED_TABLES if t != "report"]
    command.upgrade(alembic_config(db_url), "head")
    assert sorted(inspect(engine).get_table_names()) == EXPECTED_TABLES
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM report")).scalar() == 0


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
