"""Every new route: happy path, 401 without a token, 403 for the viewer on every write route,
405 on PUT/PATCH/DELETE of decisions and audit-log entries."""

import pytest
from conftest import SHARED_TOKEN, bearer
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import seed
from app.db import engine_for
from app.main import app
from app.models import User
from app.settings import get_settings

ABC_SHA256 = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"  # sha256(b"abc")

# (method, url, json body or raw bytes, minimum role)
WRITE_ROUTES = [
    ("POST", "/api/organizations", {"name": "KazTransOil"}, "admin"),
    ("POST", "/api/sites", {"org_id": 1, "name": "Pump station 3"}, "admin"),
    ("POST", "/api/inspections", {"site_id": 1, "mode": "monitoring"}, "engineer"),
    ("POST", "/api/inspections/1/video", {"name": "a.mp4", "size": 3}, "engineer"),
    ("PUT", "/api/inspections/1/video/chunks?upload_id=1&offset=0", b"abc", "engineer"),
    (
        "POST",
        "/api/inspections/1/video/complete",
        {"upload_id": 1, "sha256": ABC_SHA256},
        "engineer",
    ),
    ("POST", "/api/users", {"org_id": 1, "name": "aidana", "role": "engineer"}, "admin"),
    (
        "POST",
        "/api/inspections/1/findings",
        {"class": "corrosion", "severity": "watch", "point_indices": [0]},
        "engineer",
    ),
    ("POST", "/api/inspections/1/runs", {}, "engineer"),
    ("POST", "/api/inspections/1/runs/r1/steps/sparse/rerun", None, "engineer"),
    ("POST", "/api/findings/1/decisions", {"verdict": "confirm"}, "engineer"),
]
READ_ROUTES = [
    ("GET", "/api/organizations", "viewer"),
    ("GET", "/api/sites", "viewer"),
    ("GET", "/api/inspections", "viewer"),
    ("GET", "/api/inspections/1", "viewer"),
    ("GET", "/api/inspections/1/runs", "viewer"),
    ("GET", "/api/inspections/1/video/chunks?upload_id=1", "viewer"),
    ("GET", "/api/inspections/1/findings", "viewer"),
    ("GET", "/api/findings/1", "viewer"),
    ("GET", "/api/findings/1/decisions", "viewer"),
    ("GET", "/api/inspections/1/coverage", "viewer"),
    ("GET", "/api/me", "viewer"),
    ("GET", "/api/users", "admin"),
    ("GET", "/api/audit-log", "admin"),
]
IMMUTABLE = [
    (method, url)
    for url in ("/api/decisions/1", "/api/audit-log/1")
    for method in ("PUT", "PATCH", "DELETE")
]


def send(c: TestClient, method: str, url: str, body: object, headers: dict[str, str]) -> int:
    if isinstance(body, bytes):
        r = c.request(method, url, content=body, headers=headers)
    else:
        r = c.request(method, url, json=body, headers=headers)
    status: int = r.status_code
    return status


@pytest.mark.parametrize(("method", "url", "body", "_role"), WRITE_ROUTES)
def test_viewer_cannot_write(
    client: TestClient, method: str, url: str, body: object, _role: str
) -> None:
    assert send(client, method, url, body, headers=bearer("viewer")) == 403
    assert send(client, method, url, body, headers=bearer(SHARED_TOKEN)) == 403


@pytest.mark.parametrize(("method", "url", "body", "role"), WRITE_ROUTES)
def test_engineer_cannot_use_admin_routes(
    client: TestClient, method: str, url: str, body: object, role: str
) -> None:
    status = send(client, method, url, body, headers=bearer("engineer"))
    assert (status == 403) == (role == "admin"), status


@pytest.mark.parametrize(
    ("method", "url"), [(m, u) for m, u, _, _ in WRITE_ROUTES] + [(m, u) for m, u, _ in READ_ROUTES]
)
def test_401_without_or_with_unknown_token(client: TestClient, method: str, url: str) -> None:
    assert client.request(method, url).status_code == 401
    assert client.request(method, url, headers=bearer("not-a-token")).status_code == 401
    assert client.request(method, url, headers={"Authorization": "Basic abc"}).status_code == 401


@pytest.mark.parametrize(("method", "url"), IMMUTABLE)
def test_decisions_and_audit_log_cannot_be_edited(
    client: TestClient, method: str, url: str
) -> None:
    assert client.request(method, url, headers=bearer("viewer")).status_code == 405
    assert client.request(method, url, headers=bearer("admin")).status_code == 405
    assert client.request(method, url).status_code == 401


def test_shared_dev_token_reads_runs_and_data(client: TestClient) -> None:
    assert client.get("/api/runs", headers=bearer(SHARED_TOKEN)).status_code == 200
    assert client.get("/api/runs").status_code == 401
    assert client.get("/api/organizations", headers=bearer(SHARED_TOKEN)).json() == [
        {"id": 1, "name": "dev"}
    ]
    assert client.get("/api/users", headers=bearer(SHARED_TOKEN)).status_code == 403


def test_organizations_and_sites(client: TestClient) -> None:
    r = client.post("/api/organizations", json={"name": "KazTransOil"}, headers=bearer("admin"))
    assert r.status_code == 201
    assert r.json() == {"id": 2, "name": "KazTransOil"}
    r = client.post("/api/organizations", json={"name": "KazTransOil"}, headers=bearer("admin"))
    assert r.status_code == 409
    assert client.get("/api/organizations", headers=bearer("viewer")).json() == [
        {"id": 1, "name": "dev"},
        {"id": 2, "name": "KazTransOil"},
    ]
    r = client.post(
        "/api/sites", json={"org_id": 2, "name": "Pump station 3"}, headers=bearer("admin")
    )
    assert r.status_code == 201
    assert r.json() == {"id": 2, "org_id": 2, "name": "Pump station 3"}
    r = client.post("/api/sites", json={"org_id": 99, "name": "x"}, headers=bearer("admin"))
    assert r.status_code == 404
    assert client.get("/api/sites", headers=bearer("viewer")).json() == [
        {"id": 1, "org_id": 1, "name": "Tank farm 7"},
        {"id": 2, "org_id": 2, "name": "Pump station 3"},
    ]


def test_inspections(client: TestClient) -> None:
    r = client.post(
        "/api/inspections", json={"site_id": 1, "mode": "monitoring"}, headers=bearer("engineer")
    )
    assert r.status_code == 201
    body = r.json()
    assert body["created_at"]
    del body["created_at"]
    assert body == {"id": 2, "site_id": 1, "mode": "monitoring", "status": "created"}
    r = client.post(
        "/api/inspections", json={"site_id": 1, "mode": "bogus"}, headers=bearer("engineer")
    )
    assert r.status_code == 422
    r = client.post(
        "/api/inspections", json={"site_id": 9, "mode": "quick_scan"}, headers=bearer("engineer")
    )
    assert r.status_code == 404
    listed = client.get("/api/inspections", headers=bearer("viewer")).json()
    assert [(i["id"], i["mode"]) for i in listed] == [(1, "quick_scan"), (2, "monitoring")]
    one = client.get("/api/inspections/1", headers=bearer("viewer")).json()
    assert (one["id"], one["site_id"], one["mode"], one["status"]) == (
        1,
        1,
        "quick_scan",
        "created",
    )
    assert client.get("/api/inspections/99", headers=bearer("viewer")).status_code == 404
    assert client.get("/api/inspections/1/runs", headers=bearer("viewer")).json() == []
    assert client.get("/api/inspections/99/runs", headers=bearer("viewer")).status_code == 404


def test_users_and_audit_log(client: TestClient) -> None:
    r = client.post(
        "/api/users",
        json={"org_id": 1, "name": "aidana", "role": "engineer"},
        headers=bearer("admin"),
    )
    assert r.status_code == 201
    created = r.json()
    token = created.pop("token")
    assert created == {"id": 4, "org_id": 1, "name": "aidana", "role": "engineer"}
    assert len(token) >= 32
    # The new token works with its role: engineer writes inspections, cannot manage users.
    r = client.post(
        "/api/inspections", json={"site_id": 1, "mode": "quick_scan"}, headers=bearer(token)
    )
    assert r.status_code == 201
    assert client.get("/api/users", headers=bearer(token)).status_code == 403
    r = client.post(
        "/api/users",
        json={"org_id": 1, "name": "aidana", "role": "viewer"},
        headers=bearer("admin"),
    )
    assert r.status_code == 409
    users = client.get("/api/users", headers=bearer("admin")).json()
    assert [(u["id"], u["name"], u["role"]) for u in users] == [
        (1, "admin", "admin"),
        (2, "engineer", "engineer"),
        (3, "viewer", "viewer"),
        (4, "aidana", "engineer"),
    ]
    assert all(set(u) == {"id", "org_id", "name", "role"} for u in users)  # no secrets

    log = client.get("/api/audit-log", headers=bearer("admin")).json()
    assert [
        (e["user_id"], e["route"], e["object_type"], e["object_id"], e["action"]) for e in log
    ] == [
        (4, "POST /api/inspections", "inspection", "2", "create"),
        (1, "POST /api/users", "user", "4", "create"),
    ]
    assert all(e["created_at"] for e in log)
    assert client.get("/api/audit-log", headers=bearer("engineer")).status_code == 403


def test_seed_command_refuses_outside_dev(db_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("QARAU_ENV", raising=False)
    get_settings.cache_clear()
    with pytest.raises(SystemExit, match="QARAU_ENV"):
        seed.main()
    monkeypatch.setenv("QARAU_ENV", "dev")
    get_settings.cache_clear()
    with pytest.raises(SystemExit, match="QARAU_DEV_ADMIN_TOKEN"):
        seed.main()
    monkeypatch.setenv("QARAU_DEV_ADMIN_TOKEN", "a1")
    monkeypatch.setenv("QARAU_DEV_ENGINEER_TOKEN", "e1")
    monkeypatch.setenv("QARAU_DEV_VIEWER_TOKEN", "v1")
    get_settings.cache_clear()
    seed.main()
    seed.main()  # idempotent: same three users, secrets rotated
    with Session(engine_for(db_url)) as db:
        assert sorted((u.id, u.name, u.role) for u in db.query(User)) == [
            (1, "admin", "admin"),
            (2, "engineer", "engineer"),
            (3, "viewer", "viewer"),
        ]
    c = TestClient(app)
    assert c.get("/api/users", headers=bearer("a1")).status_code == 200
    assert c.get("/api/users", headers=bearer("e1")).status_code == 403
    assert c.get("/api/organizations", headers=bearer("e1")).status_code == 200
    body = {"site_id": 999, "mode": "quick_scan"}
    assert c.post("/api/inspections", json=body, headers=bearer("e1")).status_code == 404
    assert c.post("/api/inspections", json=body, headers=bearer("v1")).status_code == 403
    assert c.get("/api/organizations", headers=bearer("v1")).status_code == 200
