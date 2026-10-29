"""Guest sign-in and sandbox (DEMO1 part 2, ADR 0034) on the fixture bundle of
test_findings_api.py. Inspection 1 (organization "dev") has the run shared with the event,
inspection 2 (same organization) a private one. Expected values are literals."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from conftest import bearer
from fastapi.testclient import TestClient
from httpx import Response
from PIL import Image
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from test_findings_api import BODY, ENG, POINTS, write_bundle
from test_report_api import REPORT_TIME, text_of

from app import auth, event
from app.db import engine_for
from app.main import app
from app.models import AuditLog, Decision, Finding, Guest, Inspection, Mode, Report, Run
from app.ratelimit import LIMITER
from app.reports import report_clock
from app.settings import get_settings

SHARED, PRIVATE = "shared-run", "private-run"
CREATED = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)
EXPIRES = datetime(2026, 11, 7, 4, 59, tzinfo=UTC)  # --expires 2026-11-06T23:59-05:00
DURING = datetime(2026, 11, 6, 18, 0, tzinfo=UTC)
AFTER = datetime(2026, 11, 7, 5, 0, tzinfo=UTC)
GUEST_USER = 4  # seeded users 1-3, then the event's guest user made by `create`
URL = "/api/auth/guest"


def make_event(db_url: str, name: str, org: str, max_guests: int) -> str:
    with Session(engine_for(db_url)) as db:
        return event.create(db, name, org, EXPIRES, max_guests, [SHARED])


@pytest.fixture
def code(client: TestClient, db_url: str, runs_root: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """Event "launchpad" (300 guests) sharing only SHARED; the clock is during the event."""
    write_bundle(runs_root / SHARED)
    write_bundle(runs_root / PRIVATE)
    monkeypatch.setattr(event, "now", lambda: CREATED)
    monkeypatch.setattr(auth, "now", lambda: DURING)
    with Session(engine_for(db_url)) as db:
        db.add(Inspection(site_id=1, mode=Mode.quick_scan))  # inspection 2
        db.add(Run(id=SHARED, inspection_id=1, run_dir=SHARED, status="done"))
        db.flush()
        db.add(Run(id=PRIVATE, inspection_id=2, run_dir=PRIVATE, status="done"))
        db.commit()
    return make_event(db_url, "launchpad", "LaunchPad demo", 300)


def sign_in(client: TestClient, code: str) -> Response:
    r: Response = client.post(URL, json={"code": code})
    return r


def guest(client: TestClient, code: str) -> dict[str, str]:
    r = sign_in(client, code)
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def limits(monkeypatch: pytest.MonkeyPatch, per_ip: int, per_code: int) -> None:
    monkeypatch.setenv("QARAU_GUEST_RATE_PER_IP", str(per_ip))
    monkeypatch.setenv("QARAU_GUEST_RATE_PER_CODE", str(per_code))
    get_settings.cache_clear()
    monkeypatch.setattr(LIMITER, "clock", lambda: 1000.0)


# --- sign-in -----------------------------------------------------------------------------------


def test_a_code_opens_a_guest_session_in_the_event_organization(
    client: TestClient, code: str, db_url: str
) -> None:
    r = sign_in(client, code.lower())  # typed in lower case on a phone
    assert r.status_code == 201, r.text
    body = r.json()
    token = body.pop("token")
    assert body == {
        "guest_id": 1,
        "event": "launchpad",
        "organization": "LaunchPad demo",
        "expires_at": "2026-11-07T04:59:00Z",
        "runs": [{"run_id": SHARED, "run_dir": SHARED, "inspection_id": 1}],
    }
    with Session(engine_for(db_url)) as db:
        g = db.get(Guest, 1)
        assert g is not None
        assert (g.organization_id, g.event_code_id) == (2, 1)
        assert g.token_sha256 == hashlib.sha256(token.encode()).hexdigest()  # only the hash
    me = client.get(URL, headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200 and me.json() == body
    assert client.get(URL, headers=ENG).status_code == 403  # users have no guest session


def test_an_expired_or_unknown_code_is_401(
    client: TestClient, code: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = guest(client, code)
    unknown = sign_in(client, "ZZZZZZZZ")
    assert (unknown.status_code, unknown.json()) == (401, {"detail": "unknown event code"})
    monkeypatch.setattr(auth, "now", lambda: AFTER)
    expired = sign_in(client, code)
    assert (expired.status_code, expired.json()) == (401, {"detail": "this event code has expired"})
    # a session opened during the event ends with it
    assert client.get(URL, headers=session).status_code == 401
    assert client.get(f"/api/runs/{SHARED}/manifest.json", headers=session).status_code == 401


def test_a_full_event_is_403(client: TestClient, code: str, db_url: str) -> None:
    small = make_event(db_url, "small", "Small demo", 1)
    assert sign_in(client, small).status_code == 201
    full = sign_in(client, small)
    assert (full.status_code, full.json()) == (403, {"detail": "event is full"})
    assert sign_in(client, code).status_code == 201  # another event is not affected


# --- rate limits -------------------------------------------------------------------------------


def test_over_the_per_ip_limit_is_429(
    client: TestClient, code: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    limits(monkeypatch, per_ip=3, per_code=100)
    for wrong in ("AAAAAAAA", "BBBBBBBB", "CCCCCCCC"):
        assert sign_in(client, wrong).status_code == 401
    r = sign_in(client, code)  # even the right code, from this IP
    assert (r.status_code, r.headers["retry-after"]) == (429, "60")
    other_ip = TestClient(app, client=("203.0.113.9", 50000))
    assert sign_in(other_ip, code).status_code == 201
    monkeypatch.setattr(LIMITER, "clock", lambda: 1060.0)  # the window has passed
    assert sign_in(client, code).status_code == 201


def test_over_the_per_code_limit_is_429(
    client: TestClient, code: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    limits(monkeypatch, per_ip=100, per_code=2)
    assert sign_in(client, code).status_code == 201
    assert sign_in(client, code).status_code == 201
    r = sign_in(client, code)
    assert (r.status_code, r.headers["retry-after"]) == (429, "60")
    other_ip = TestClient(app, client=("203.0.113.9", 50000))
    assert sign_in(other_ip, code).status_code == 429  # the code is limited from every IP
    assert sign_in(client, "ZZZZZZZZ").status_code == 401  # another code is not


# --- audit -------------------------------------------------------------------------------------


def test_each_code_use_writes_one_audit_row(
    client: TestClient, code: str, db_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    limits(monkeypatch, per_ip=5, per_code=100)
    small = make_event(db_url, "small", "Small demo", 1)
    assert sign_in(client, code).status_code == 201
    assert sign_in(client, "ZZZZZZZZ").status_code == 401
    assert sign_in(client, small).status_code == 201
    assert sign_in(client, small).status_code == 403
    monkeypatch.setattr(auth, "now", lambda: AFTER)
    assert sign_in(client, code).status_code == 401
    assert sign_in(client, code).status_code == 429
    with Session(engine_for(db_url)) as db:
        rows = db.scalars(
            select(AuditLog).where(AuditLog.action == "event_code.use").order_by(AuditLog.id)
        ).all()
        assert len(rows) == 6
        assert [(a.user_id, a.route, a.object_type) for a in rows] == [
            (None, "POST /api/auth/guest", "event_code")
        ] * 6
        assert [(a.object_id, a.detail) for a in rows] == [
            ("launchpad", {"result": "ok", "ip": "testclient"}),
            (None, {"result": "unknown", "ip": "testclient"}),
            ("small", {"result": "ok", "ip": "testclient"}),
            ("small", {"result": "full", "ip": "testclient"}),
            ("launchpad", {"result": "expired", "ip": "testclient"}),
            (None, {"result": "rate_limited", "ip": "testclient"}),
        ]
    audit = client.get("/api/audit-log", headers=bearer("admin")).json()
    assert audit[0]["detail"] == {"result": "rate_limited", "ip": "testclient"}


# --- what a guest may not do -------------------------------------------------------------------


def test_a_guest_upload_or_processing_request_is_403(
    client: TestClient, code: str, db_url: str
) -> None:
    g = guest(client, code)
    video = {"name": "walkdown.mp4", "size": 1000}
    assert client.post("/api/inspections/1/video", json=video, headers=g).status_code == 403
    assert client.post("/api/inspections/1/runs", json={}, headers=g).status_code == 403
    rerun = f"/api/inspections/1/runs/{SHARED}/steps/frames/rerun"
    assert client.post(rerun, headers=g).status_code == 403
    for path, body in (
        ("/api/organizations", {"name": "x"}),
        ("/api/sites", {"org_id": 1, "name": "x"}),
        ("/api/users", {"org_id": 2, "name": "x", "role": "admin"}),
        ("/api/inspections", {"site_id": 1, "mode": "quick_scan"}),
    ):
        assert client.post(path, json=body, headers=g).status_code == 403, path
    with engine_for(db_url).connect() as conn:
        runs = conn.execute(text("SELECT id FROM run ORDER BY id")).scalars().all()
        assert runs == [PRIVATE, SHARED]
        assert conn.execute(text("SELECT count(*) FROM video_upload")).scalar() == 0
        assert conn.execute(text('SELECT count(*) FROM "user"')).scalar() == 4


def test_a_guest_gets_404_for_an_unlisted_run_and_sees_no_other_inspections(
    client: TestClient, code: str
) -> None:
    g = guest(client, code)
    assert client.get(f"/api/runs/{PRIVATE}/manifest.json", headers=g).status_code == 404
    points = "files/web/points.position.f32"
    assert client.get(f"/api/runs/{PRIVATE}/{points}", headers=g).status_code == 404
    assert client.get(f"/api/runs/{SHARED}/manifest.json", headers=g).status_code == 200
    assert client.get(f"/api/runs/{SHARED}/{points}", headers=g).status_code == 200
    assert [r["run_id"] for r in client.get("/api/runs", headers=g).json()] == [SHARED]
    assert [r["run_id"] for r in client.get("/api/runs", headers=ENG).json()] == [PRIVATE, SHARED]
    for path in (
        "/api/inspections/2",
        "/api/inspections/2/findings",
        "/api/inspections/2/coverage",
        "/api/inspections/2/report.pdf?lang=en",
    ):
        assert client.get(path, headers=g).status_code == 404, path
    assert client.post("/api/inspections/2/findings", json=BODY, headers=g).status_code == 404
    # no listing of inspections, organizations, sites, users or runs of an inspection
    for path in (
        "/api/inspections",
        "/api/organizations",
        "/api/sites",
        "/api/users",
        "/api/audit-log",
        "/api/inspections/1/runs",
        "/api/me",
    ):
        assert client.get(path, headers=g).status_code == 403, path
    assert client.get("/api/inspections/1", headers=g).json()["id"] == 1  # the shared one


# --- the sandbox -------------------------------------------------------------------------------


def test_guest_a_reading_guest_b_finding_is_404(client: TestClient, code: str, db_url: str) -> None:
    a, b = guest(client, code), guest(client, code)
    r = client.post("/api/inspections/1/findings", json=BODY, headers=a)
    assert (r.status_code, r.json()["id"]) == (201, 1)
    assert client.post("/api/inspections/1/findings", json=BODY, headers=b).json()["id"] == 2
    assert client.get("/api/findings/2", headers=a).status_code == 404
    assert client.get("/api/findings/2/decisions", headers=a).status_code == 404
    confirm = {"verdict": "confirm"}
    assert client.post("/api/findings/2/decisions", json=confirm, headers=a).status_code == 404
    assert [f["id"] for f in client.get("/api/inspections/1/findings", headers=a).json()] == [1]
    assert client.get("/api/findings/1", headers=a).status_code == 200
    d = client.post("/api/findings/1/decisions", json=confirm, headers=a)
    assert d.status_code == 201
    assert (d.json()["user_id"], d.json()["user"]) == (GUEST_USER, "guest")
    # users never see a guest's rows
    assert client.get("/api/findings/1", headers=ENG).status_code == 404
    assert client.get("/api/findings/1/decisions", headers=ENG).status_code == 404
    assert client.get("/api/inspections/1/findings", headers=ENG).json() == []
    with Session(engine_for(db_url)) as db:
        found = [(f.id, f.guest_id) for f in db.scalars(select(Finding).order_by(Finding.id))]
        assert found == [(1, 1), (2, 2)]
        dec = db.scalars(select(Decision)).one()
        assert (dec.finding_id, dec.user_id, dec.guest_id) == (1, GUEST_USER, 1)


def test_a_guest_pdf_has_only_its_own_findings_and_decisions(
    client: TestClient, code: str, db_url: str, runs_root: Path
) -> None:
    run = runs_root / SHARED  # the colour layer and frame images the report draws
    (run / "web/points.color.u8").write_bytes(bytes([90, 90, 90] * len(POINTS)))
    man = json.loads((run / "web/manifest.json").read_text())
    man["points"]["color"] = "web/points.color.u8"
    (run / "web/manifest.json").write_text(json.dumps(man))
    (run / "frames").mkdir()
    for i in range(4):
        Image.new("RGB", (100, 100), (40 * i, 80, 120)).save(run / f"frames/{i:06d}.jpg")
    assert client.post("/api/inspections/1/findings", json=BODY, headers=ENG).json()["id"] == 1
    a, b = guest(client, code), guest(client, code)
    assert client.post("/api/inspections/1/findings", json=BODY, headers=a).json()["id"] == 2
    assert client.post("/api/inspections/1/findings", json=BODY, headers=b).json()["id"] == 3
    reject = {"verdict": "reject", "comment": "guest A says shadow"}
    assert client.post("/api/findings/2/decisions", json=reject, headers=a).status_code == 201
    app.dependency_overrides[report_clock] = lambda: REPORT_TIME
    pdf = {}
    for who, headers in (("a", a), ("engineer", ENG)):
        r = client.get("/api/inspections/1/report.pdf", params={"lang": "en"}, headers=headers)
        assert r.status_code == 200, r.text
        pdf[who] = text_of(r.content)
    assert "Finding 2" in pdf["a"] and "guest A says shadow" in pdf["a"]
    assert "Finding 1" not in pdf["a"] and "Finding 3" not in pdf["a"]
    assert "Finding 1" in pdf["engineer"]
    assert "Finding 2" not in pdf["engineer"] and "Finding 3" not in pdf["engineer"]
    assert "guest A says shadow" not in pdf["engineer"]
    with Session(engine_for(db_url)) as db:
        rows = db.scalars(select(Report).order_by(Report.id)).all()
        assert [(r.created_by, r.guest_id) for r in rows] == [(GUEST_USER, 1), (2, None)]


def test_openapi_declares_the_guest_routes() -> None:
    paths = TestClient(app).get("/openapi.json").json()["paths"]
    assert paths[URL]["post"]["description"].startswith("Role: public sign-in")
    assert set(paths[URL]["post"]["responses"]) >= {"201", "401", "403", "429"}
    assert paths[URL]["get"]["description"].startswith("Role: guest.")
