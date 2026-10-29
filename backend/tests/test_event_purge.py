"""`python -m app.event purge` (DEMO1 part 2, ADR 0034): deletes the guest data of one event
organization in one transaction and nothing else. Setup from test_guest_api.py; expected
values are literals."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_findings_api import BODY, ENG
from test_guest_api import SHARED, code, guest  # noqa: F401  (fixture)

from app import event
from app.db import engine_for

COUNTS = {
    "guests": "SELECT count(*) FROM guest",
    "guest sessions": "SELECT count(*) FROM guest WHERE token_sha256 IS NOT NULL",
    "guest findings": "SELECT count(*) FROM finding WHERE guest_id IS NOT NULL",
    "guest observations": "SELECT count(*) FROM observation WHERE finding_id IN "
    "(SELECT id FROM finding WHERE guest_id IS NOT NULL)",
    "guest decisions": "SELECT count(*) FROM decision WHERE guest_id IS NOT NULL",
    "guest reports": "SELECT count(*) FROM report WHERE guest_id IS NOT NULL",
    "findings": "SELECT count(*) FROM finding",
    "decisions": "SELECT count(*) FROM decision",
    "audit rows": "SELECT count(*) FROM audit_log",
    "shared run": f"SELECT count(*) FROM run WHERE id = '{SHARED}'",  # noqa: S608
    "event codes": "SELECT count(*) FROM event_code",
    "event organizations": "SELECT count(*) FROM organization WHERE is_event",
    "users": 'SELECT count(*) FROM "user"',
}


def counts(db_url: str) -> dict[str, int]:
    with engine_for(db_url).connect() as conn:
        return {k: conn.execute(text(sql)).scalar_one() for k, sql in COUNTS.items()}


@pytest.fixture
def used(client: TestClient, code: str) -> TestClient:  # noqa: F811
    """An engineer's finding 1 with a decision; guests A and B each sign in, mark a finding (2,
    3) and decide on it. Audit rows: 2 by the engineer, 2 code uses, 4 by the guests."""
    assert client.post("/api/inspections/1/findings", json=BODY, headers=ENG).json()["id"] == 1
    confirm = {"verdict": "confirm"}
    assert client.post("/api/findings/1/decisions", json=confirm, headers=ENG).status_code == 201
    for n, g in enumerate((guest(client, code), guest(client, code)), start=2):
        assert client.post("/api/inspections/1/findings", json=BODY, headers=g).json()["id"] == n
        r = client.post(f"/api/findings/{n}/decisions", json=confirm, headers=g)
        assert r.status_code == 201
    return client


def add_guest_reports(db_url: str) -> None:
    """One report row per guest, as GET .../report.pdf writes it (the PDF itself is tested in
    test_guest_api.py)."""
    with engine_for(db_url).begin() as conn:
        for g in (1, 2):
            conn.execute(
                text(
                    "INSERT INTO report (inspection_id, lang, sha256, generated_at, "
                    "report_number, version, created_by, guest_id) VALUES (1, 'en', :sha, "
                    "CURRENT_TIMESTAMP, 'QV-2026-0001', 1, 4, :g)"
                ),
                {"sha": str(g) * 64, "g": g},
            )


def test_purge_deletes_every_guest_row_and_keeps_the_rest(
    used: TestClient, db_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    add_guest_reports(db_url)
    before = counts(db_url)
    assert before == {
        "guests": 2, "guest sessions": 2, "guest findings": 2, "guest observations": 6,
        "guest decisions": 2, "guest reports": 2, "findings": 3, "decisions": 3,
        "audit rows": 8, "shared run": 1, "event codes": 1, "event organizations": 1, "users": 4,
    }  # fmt: skip

    event.main(["purge", "--name", "launchpad"])

    out = capsys.readouterr().out
    assert (
        "event 'launchpad' purged: 2 sessions, 2 decisions, 6 observations, 2 findings, "
        "2 reports, 2 guests" in out
    )
    assert counts(db_url) == {
        "guests": 0, "guest sessions": 0, "guest findings": 0, "guest observations": 0,
        "guest decisions": 0, "guest reports": 0, "findings": 1, "decisions": 1,
        "audit rows": 8, "shared run": 1, "event codes": 1, "event organizations": 1, "users": 4,
    }  # fmt: skip
    # the engineer's finding and decision are untouched and still readable
    assert used.get("/api/findings/1/decisions", headers=ENG).json()[0]["verdict"] == "confirm"


def test_after_purge_a_normal_decision_still_cannot_be_deleted_or_updated(
    used: TestClient, db_url: str
) -> None:
    event.main(["purge", "--name", "launchpad"])
    engine = engine_for(db_url)
    for sql in ("DELETE FROM decision", "UPDATE decision SET comment = 'edited'"):
        with pytest.raises(DBAPIError, match="append-only"), engine.begin() as conn:
            conn.execute(text(sql))
    assert counts(db_url)["decisions"] == 1


def test_purge_of_an_unknown_event_exits_2_and_deletes_nothing(
    used: TestClient, db_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    before = counts(db_url)
    with pytest.raises(SystemExit) as exc:
        event.main(["purge", "--name", "no-such-event"])
    assert exc.value.code == 2
    assert "unknown event 'no-such-event'" in capsys.readouterr().err
    assert counts(db_url) == before


def test_purge_refuses_an_event_code_outside_an_event_organization(
    used: TestClient, db_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """Defence in depth: the 0007 trigger keeps event codes in event organizations; with it
    dropped (as only direct SQL could), purge still checks the flag itself and refuses."""
    engine = engine_for(db_url)
    drop = {
        "sqlite": "DROP TRIGGER event_code_event_org_insert",
        "postgresql": "DROP TRIGGER event_code_event_org_only ON event_code",
    }[engine.dialect.name]
    with engine.begin() as conn:
        conn.execute(text(drop))
        conn.execute(
            text(
                "INSERT INTO event_code (organization_id, name, code_sha256, expires_at, "
                "max_guests, created_at) VALUES (1, 'customer', :h, CURRENT_TIMESTAMP, 1, "
                "CURRENT_TIMESTAMP)"
            ),
            {"h": "c" * 64},
        )
    before = counts(db_url)
    with pytest.raises(SystemExit) as exc:
        event.main(["purge", "--name", "customer"])
    assert exc.value.code == 2
    assert "not an event organization; refused" in capsys.readouterr().err
    assert counts(db_url) == before
