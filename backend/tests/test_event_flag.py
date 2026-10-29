"""DEMO1 part 1 (migration 0007): `organization.is_event` cannot change after INSERT, guests and
codes live only in event organizations, and a decision stays append-only everywhere except a
guest's decision in an event organization (the one thing purge may delete)."""

from datetime import UTC, datetime

import pytest
from conftest import bearer
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.db import engine_for
from app.models import (
    AuditLog,
    Decision,
    EventCode,
    Finding,
    Guest,
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

SET_FLAG = "UPDATE organization SET is_event = :flag WHERE id = :id"


def make_org(db: Session, name: str, is_event: bool) -> tuple[Organization, User]:
    org = Organization(name=name, is_event=is_event)
    db.add(org)
    db.flush()
    user = User(org_id=org.id, name="u", role=Role.engineer, secret_hash="h", salt="s")
    db.add(user)
    db.flush()
    return org, user


def make_world(db_url: str) -> dict[str, int]:
    """A normal org with a done run, a finding and a decision; an event org with a code and a
    guest. Ids of the interesting rows."""
    with Session(engine_for(db_url)) as db:
        org, user = make_org(db, "Normal Co", False)
        ev_org, ev_user = make_org(db, "LaunchPad demo", True)
        site = Site(org_id=org.id, name="s")
        db.add(site)
        db.flush()
        insp = Inspection(site_id=site.id, mode=Mode.quick_scan)
        db.add(insp)
        db.flush()
        db.add(Run(id="r1", inspection_id=insp.id, run_dir="r1", status="done"))
        db.flush()
        finding = Finding(run_id="r1", class_="corrosion", severity=Severity.watch, points=[])
        db.add(finding)
        db.flush()
        code = EventCode(
            organization_id=ev_org.id,
            name="launchpad",
            code_sha256="c" * 64,
            expires_at=datetime(2026, 11, 7, 4, 59, tzinfo=UTC),
            max_guests=300,
        )
        db.add(code)
        db.flush()
        guest = Guest(organization_id=ev_org.id, event_code_id=code.id)
        db.add(guest)
        db.flush()
        normal = Decision(finding_id=finding.id, user_id=user.id, verdict=Verdict.confirm)
        db.add(normal)
        db.add(AuditLog(user_id=ev_user.id, route="POST /x", object_type="x", action="create"))
        db.commit()
        return {
            "org": org.id,
            "event_org": ev_org.id,
            "user": user.id,
            "event_user": ev_user.id,
            "finding": finding.id,
            "code": code.id,
            "guest": guest.id,
            "normal_decision": normal.id,
        }


def add_decision(db_url: str, finding: int, user: int, guest: int | None) -> int:
    with Session(engine_for(db_url)) as db:
        d = Decision(finding_id=finding, user_id=user, verdict=Verdict.reject, guest_id=guest)
        db.add(d)
        db.commit()
        return d.id


def decision_count(db_url: str) -> int:
    with engine_for(db_url).connect() as conn:
        return int(conn.execute(text("SELECT count(*) FROM decision")).scalar_one())


# --- the event flag ---------------------------------------------------------------------------


def test_the_event_flag_cannot_be_set_on_a_normal_organization(db_url: str) -> None:
    ids = make_world(db_url)
    engine = engine_for(db_url)
    with pytest.raises(DBAPIError, match="is_event is immutable"), engine.begin() as conn:
        conn.execute(text(SET_FLAG), {"flag": True, "id": ids["org"]})
    with Session(engine) as db:  # through the ORM too
        org = db.get(Organization, ids["org"])
        assert org is not None
        org.is_event = True
        with pytest.raises(DBAPIError, match="is_event is immutable"):
            db.commit()
    with engine.connect() as conn:
        flag = conn.execute(
            text("SELECT is_event FROM organization WHERE id = :id"), {"id": ids["org"]}
        )
        assert flag.scalar() in (False, 0)


def test_the_event_flag_cannot_be_cleared_on_an_event_organization(db_url: str) -> None:
    ids = make_world(db_url)
    engine = engine_for(db_url)
    with pytest.raises(DBAPIError, match="is_event is immutable"), engine.begin() as conn:
        conn.execute(text(SET_FLAG), {"flag": False, "id": ids["event_org"]})
    with engine.connect() as conn:
        flag = conn.execute(
            text("SELECT is_event FROM organization WHERE id = :id"), {"id": ids["event_org"]}
        )
        assert flag.scalar() in (True, 1)


def test_other_organization_columns_still_update(db_url: str) -> None:
    ids = make_world(db_url)
    with engine_for(db_url).begin() as conn:
        conn.execute(
            text("UPDATE organization SET name = 'Renamed' WHERE id = :id"), {"id": ids["org"]}
        )
        conn.execute(text(SET_FLAG), {"flag": False, "id": ids["org"]})  # unchanged value: fine
    with Session(engine_for(db_url)) as db:
        org = db.get(Organization, ids["org"])
        assert org is not None and (org.name, org.is_event) == ("Renamed", False)


def test_the_api_cannot_create_an_event_organization(client: TestClient, db_url: str) -> None:
    r = client.post(
        "/api/organizations", json={"name": "Sneaky", "is_event": True}, headers=bearer("admin")
    )
    assert r.status_code == 201
    with Session(engine_for(db_url)) as db:
        org = db.get(Organization, r.json()["id"])
        assert org is not None and (org.name, org.is_event) == ("Sneaky", False)


def test_guests_and_codes_live_only_in_event_organizations(db_url: str) -> None:
    ids = make_world(db_url)
    engine = engine_for(db_url)
    with pytest.raises(DBAPIError, match="event organization"), engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO event_code (organization_id, name, code_sha256, expires_at, "
                "max_guests, created_at) VALUES (:org, 'x', :h, CURRENT_TIMESTAMP, 1, "
                "CURRENT_TIMESTAMP)"
            ),
            {"org": ids["org"], "h": "d" * 64},
        )
    with pytest.raises(DBAPIError, match="event organization"), engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO guest (organization_id, event_code_id, created_at) "
                "VALUES (:org, :code, CURRENT_TIMESTAMP)"
            ),
            {"org": ids["org"], "code": ids["code"]},
        )
    with pytest.raises(DBAPIError, match="event organization"), engine.begin() as conn:
        conn.execute(
            text("UPDATE guest SET organization_id = :org WHERE id = :id"),
            {"org": ids["org"], "id": ids["guest"]},
        )
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM guest")).scalar() == 1
        assert conn.execute(text("SELECT count(*) FROM event_code")).scalar() == 1


# --- decision and audit_log stay append-only --------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE decision SET comment = 'edited' WHERE id = :id",
        "DELETE FROM decision WHERE id = :id",
    ],
)
def test_a_decision_in_a_normal_organization_cannot_be_updated_or_deleted(
    db_url: str, sql: str
) -> None:
    ids = make_world(db_url)
    with pytest.raises(DBAPIError, match="append-only"), engine_for(db_url).begin() as conn:
        conn.execute(text(sql), {"id": ids["normal_decision"]})
    with Session(engine_for(db_url)) as db:
        d = db.get(Decision, ids["normal_decision"])
        assert d is not None and (d.verdict, d.comment) == (Verdict.confirm, None)


def test_a_normal_user_decision_tagged_with_a_guest_cannot_be_deleted(db_url: str) -> None:
    """guest_id alone does not open the door: the decider must be a user of the guest's event
    organization."""
    ids = make_world(db_url)
    tagged = add_decision(db_url, ids["finding"], ids["user"], ids["guest"])
    with pytest.raises(DBAPIError, match="append-only"), engine_for(db_url).begin() as conn:
        conn.execute(text("DELETE FROM decision WHERE id = :id"), {"id": tagged})
    assert decision_count(db_url) == 2


def test_a_guest_decision_in_an_event_organization_can_be_deleted_not_updated(
    db_url: str,
) -> None:
    ids = make_world(db_url)
    guest_decision = add_decision(db_url, ids["finding"], ids["event_user"], ids["guest"])
    no_guest = add_decision(db_url, ids["finding"], ids["event_user"], None)
    engine = engine_for(db_url)
    with pytest.raises(DBAPIError, match="append-only"), engine.begin() as conn:
        conn.execute(
            text("UPDATE decision SET comment = 'edited' WHERE id = :id"), {"id": guest_decision}
        )
    with pytest.raises(DBAPIError, match="append-only"), engine.begin() as conn:
        conn.execute(text("DELETE FROM decision WHERE id = :id"), {"id": no_guest})
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM decision WHERE id = :id"), {"id": guest_decision})
    with engine.connect() as conn:
        left = conn.execute(text("SELECT id FROM decision ORDER BY id")).scalars().all()
    assert left == [ids["normal_decision"], no_guest]


@pytest.mark.parametrize("sql", ["UPDATE audit_log SET action = 'edited'", "DELETE FROM audit_log"])
def test_audit_log_stays_append_only_in_an_event_organization(db_url: str, sql: str) -> None:
    make_world(db_url)  # its only audit row is by the event organization's user
    with pytest.raises(DBAPIError, match="append-only"), engine_for(db_url).begin() as conn:
        conn.execute(text(sql))
    with engine_for(db_url).connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM audit_log")).scalar() == 1
