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
    "coverage_region",
    "decision",
    "event_code",
    "event_code_run",
    "finding",
    "guest",
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
DECISION_COLUMNS_HEAD = sorted([*DECISION_COLUMNS_0004, "guest_id"])
EVENT_TABLES = ("event_code", "event_code_run", "guest")


def test_v0004_adds_reclass_columns_and_keeps_the_append_only_trigger(db_url: str) -> None:
    """0004 adds `new_class` / `new_severity` to `decision` with plain ADD COLUMN; the 0001
    trigger survives the upgrade and the downgrade (batch mode would have recreated the table)."""
    from conftest import alembic_config

    from alembic import command

    engine = engine_for(db_url)
    cols = {c["name"]: c for c in inspect(engine).get_columns("decision")}
    assert sorted(cols) == DECISION_COLUMNS_HEAD
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
        sorted(c["name"] for c in inspect(engine).get_columns("decision")) == DECISION_COLUMNS_HEAD
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


REPORT_COLUMNS = ["created_by", "generated_at", "guest_id", "id", "inspection_id", "lang",
                  "report_number", "sha256", "version"]  # fmt: skip


def test_v0005_creates_the_report_table_and_downgrades(db_url: str) -> None:
    """0005 adds `report` (one row per generated PDF) with foreign keys to inspection and user;
    downgrade to 0004 drops it and leaves the other tables, upgrade brings it back."""
    from conftest import alembic_config

    from alembic import command

    engine = engine_for(db_url)
    insp = inspect(engine)
    cols = {c["name"]: c for c in insp.get_columns("report")}
    assert sorted(cols) == REPORT_COLUMNS
    assert cols["created_by"]["nullable"] and cols["guest_id"]["nullable"]
    assert not any(
        cols[c]["nullable"] for c in REPORT_COLUMNS if c not in ("created_by", "guest_id")
    )
    assert getattr(cols["sha256"]["type"], "length", None) == 64
    fks = {
        fk["constrained_columns"][0]: fk["referred_table"] for fk in insp.get_foreign_keys("report")
    }
    expected_fks = {"inspection_id": "inspection", "created_by": "user"}
    if engine.dialect.name == "postgresql":  # SQLite: guest_id has no foreign key (0007)
        expected_fks["guest_id"] = "guest"
    assert fks == expected_fks
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
    assert tables == [
        t for t in EXPECTED_TABLES if t not in ("report", "coverage_region", *EVENT_TABLES)
    ]
    command.upgrade(alembic_config(db_url), "head")
    assert sorted(inspect(engine).get_table_names()) == EXPECTED_TABLES
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM report")).scalar() == 0


COVERAGE_COLUMNS = ["bounds_max", "bounds_min", "created_at", "frame_count", "id", "region_key",
                    "run_id", "state"]  # fmt: skip
REGION_SQL = ("INSERT INTO coverage_region (run_id, region_key, state, frame_count, bounds_min, "
              "bounds_max, created_at) VALUES (:run, :key, :state, 3, '[0, 0, 0]', '[1, 1, 1]', "
              "CURRENT_TIMESTAMP)")  # fmt: skip


def test_v0006_creates_the_coverage_region_table_and_downgrades(db_url: str) -> None:
    """0006 adds `coverage_region` (a run's map regions) with a run foreign key, one row per
    (run, region key) and only the three coverage states; downgrade to 0005 drops it and
    leaves every other table, upgrade brings it back empty."""
    from conftest import alembic_config

    from alembic import command

    engine = engine_for(db_url)
    insp = inspect(engine)
    cols = {c["name"]: c for c in insp.get_columns("coverage_region")}
    assert sorted(cols) == COVERAGE_COLUMNS
    assert not any(c["nullable"] for c in cols.values() if c["name"] != "id")
    fks = [(fk["constrained_columns"], fk["referred_table"])
           for fk in insp.get_foreign_keys("coverage_region")]  # fmt: skip
    assert fks == [(["run_id"], "run")]
    seed_one_decision_and_audit_row(db_url)  # run "rrr…"
    run = "r" * 32
    with engine.begin() as conn:
        conn.execute(text(REGION_SQL), {"run": run, "key": "0_0_0", "state": "not_seen"})
    with pytest.raises(DBAPIError), engine.begin() as conn:  # same region of the same run
        conn.execute(text(REGION_SQL), {"run": run, "key": "0_0_0", "state": "finding"})
    with pytest.raises(DBAPIError), engine.begin() as conn:  # not a coverage state
        conn.execute(text(REGION_SQL), {"run": run, "key": "0_0_1", "state": "ok"})

    command.downgrade(alembic_config(db_url), "0005")
    tables = sorted(inspect(engine).get_table_names())
    assert tables == [t for t in EXPECTED_TABLES if t not in ("coverage_region", *EVENT_TABLES)]
    command.upgrade(alembic_config(db_url), "head")
    assert sorted(inspect(engine).get_table_names()) == EXPECTED_TABLES
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM coverage_region")).scalar() == 0


ROWS_0006 = [
    "INSERT INTO organization (id, name) VALUES (1, 'o')",
    'INSERT INTO "user" (id, org_id, name, role, secret_hash, salt) '
    "VALUES (1, 1, 'u', 'engineer', 'h', 's')",
    "INSERT INTO site (id, org_id, name) VALUES (1, 1, 's')",
    "INSERT INTO inspection (id, site_id, mode, status, created_at) "
    "VALUES (1, 1, 'quick_scan', 'created', CURRENT_TIMESTAMP)",
    "INSERT INTO run (id, inspection_id, run_dir, status, progress, steps) "
    "VALUES ('r1', 1, 'r1', 'done', 100, '[]')",
    "INSERT INTO finding (id, run_id, class, severity, status, area_units, points) "
    "VALUES (1, 'r1', 'corrosion', 'watch', 'proposed', 0, '[]')",
    "INSERT INTO decision (id, finding_id, user_id, verdict, created_at) "
    "VALUES (1, 1, 1, 'confirm', CURRENT_TIMESTAMP)",
]
EVENT_CODE_COLUMNS = ["code_sha256", "created_at", "expires_at", "id", "max_guests", "name",
                      "organization_id"]  # fmt: skip


def test_v0007_adds_event_tables_and_guest_columns_and_downgrades(db_url: str) -> None:
    """0007 on a database with data: the existing organization is not an event, existing rows
    get a null guest_id, the new tables exist; downgrade to 0006 removes all of it and the
    decision stays append-only before, during and after (the 0001 trigger body is restored)."""
    from conftest import alembic_config, drop_everything

    from alembic import command

    engine = engine_for(db_url)
    drop_everything(db_url)
    command.upgrade(alembic_config(db_url), "0006")
    with engine.begin() as conn:
        for sql in ROWS_0006:
            conn.execute(text(sql))
    command.upgrade(alembic_config(db_url), "0007")

    insp = inspect(engine)
    assert sorted(insp.get_table_names()) == EXPECTED_TABLES
    org_cols = {c["name"]: c for c in insp.get_columns("organization")}
    assert not org_cols["is_event"]["nullable"]
    assert sorted(c["name"] for c in insp.get_columns("event_code")) == EVENT_CODE_COLUMNS
    assert sorted(c["name"] for c in insp.get_columns("event_code_run")) == [
        "event_code_id",
        "run_id",
    ]
    assert sorted(c["name"] for c in insp.get_columns("guest")) == [
        "created_at",
        "event_code_id",
        "id",
        "organization_id",
    ]
    for table in ("finding", "decision", "report"):
        cols = {c["name"]: c for c in insp.get_columns(table)}
        assert cols["guest_id"]["nullable"], table
    with engine.connect() as conn:
        assert conn.execute(text("SELECT is_event FROM organization")).scalar() in (False, 0)
        assert conn.execute(text("SELECT guest_id FROM decision")).scalar() is None
        assert conn.execute(text("SELECT guest_id FROM finding")).scalar() is None
    with pytest.raises(DBAPIError, match="append-only"), engine.begin() as conn:
        conn.execute(text("DELETE FROM decision"))

    command.downgrade(alembic_config(db_url), "0006")
    insp = inspect(engine)
    assert sorted(insp.get_table_names()) == [t for t in EXPECTED_TABLES if t not in EVENT_TABLES]
    assert "is_event" not in {c["name"] for c in insp.get_columns("organization")}
    for table in ("finding", "decision", "report"):
        assert "guest_id" not in {c["name"] for c in insp.get_columns(table)}, table
    for sql in ("DELETE FROM decision", "UPDATE decision SET comment = 'edited'"):
        with pytest.raises(DBAPIError, match="append-only"), engine.begin() as conn:
            conn.execute(text(sql))
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM decision")).scalar() == 1

    command.upgrade(alembic_config(db_url), "head")
    with pytest.raises(DBAPIError, match="append-only"), engine.begin() as conn:
        conn.execute(text("DELETE FROM decision"))


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


AUDIT_ROW = ("INSERT INTO audit_log (id, user_id, created_at, route, object_type, action) "
             "VALUES (1, 1, CURRENT_TIMESTAMP, 'POST /x', 'x', 'create')")  # fmt: skip
ANONYMOUS_AUDIT_ROW = (
    "INSERT INTO audit_log (user_id, created_at, route, object_type, action, detail) VALUES "
    "(NULL, CURRENT_TIMESTAMP, 'POST /api/auth/guest', 'event_code', 'event_code.use', "
    """'{"result": "unknown", "ip": "203.0.113.7"}')"""
)
AUDIT_COLUMNS_0007 = ["action", "created_at", "id", "object_id", "object_type", "route", "user_id"]


def test_v0008_adds_guest_sessions_and_anonymous_audit_rows_and_downgrades(db_url: str) -> None:
    """0008 on a database with an audit row: `guest.token_sha256` (unique), `audit_log.detail`
    and a nullable `audit_log.user_id`; the row survives and audit_log stays append-only (on
    SQLite the table is rebuilt and the 0001 triggers are created again). Downgrade restores
    0007 and is refused while an anonymous audit row exists: the log is never edited to pass."""
    from conftest import alembic_config, drop_everything

    from alembic import command

    engine = engine_for(db_url)
    drop_everything(db_url)
    command.upgrade(alembic_config(db_url), "0007")
    with engine.begin() as conn:
        for sql in [*ROWS_0006, AUDIT_ROW]:
            conn.execute(text(sql))
    command.upgrade(alembic_config(db_url), "0008")

    insp = inspect(engine)
    audit = {c["name"]: c for c in insp.get_columns("audit_log")}
    assert sorted(audit) == sorted([*AUDIT_COLUMNS_0007, "detail"])
    assert audit["user_id"]["nullable"] and audit["detail"]["nullable"]
    assert [fk["referred_table"] for fk in insp.get_foreign_keys("audit_log")] == ["user"]
    guest = {c["name"]: c for c in insp.get_columns("guest")}
    assert guest["token_sha256"]["nullable"]
    assert [(i["column_names"], bool(i["unique"])) for i in insp.get_indexes("guest")] == [
        (["token_sha256"], True)
    ]
    with engine.connect() as conn:
        assert conn.execute(text("SELECT user_id, action FROM audit_log")).all() == [(1, "create")]
    for sql in FORBIDDEN_SQL[2:]:  # the audit_log statements
        with pytest.raises(DBAPIError, match="append-only"), engine.begin() as conn:
            conn.execute(text(sql))

    command.downgrade(alembic_config(db_url), "0007")
    insp = inspect(engine)
    audit = {c["name"]: c for c in insp.get_columns("audit_log")}
    assert sorted(audit) == AUDIT_COLUMNS_0007
    assert not audit["user_id"]["nullable"]
    assert "token_sha256" not in {c["name"] for c in insp.get_columns("guest")}
    for sql in FORBIDDEN_SQL[2:]:
        with pytest.raises(DBAPIError, match="append-only"), engine.begin() as conn:
            conn.execute(text(sql))

    command.upgrade(alembic_config(db_url), "head")
    with engine.begin() as conn:
        conn.execute(text(ANONYMOUS_AUDIT_ROW))
    with pytest.raises(DBAPIError):
        command.downgrade(alembic_config(db_url), "0007")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM audit_log")).scalar() == 2
