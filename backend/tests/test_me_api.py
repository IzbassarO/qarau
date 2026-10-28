"""GET /api/me: the caller's id, name, role and organization, never a secret."""

import pytest
from conftest import SHARED_TOKEN, bearer
from fastapi.testclient import TestClient

from app.main import app

SECRET_KEYS = {"secret_hash", "salt", "token"}


def all_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in all_keys(v)}
    if isinstance(value, list):
        return {k for v in value for k in all_keys(v)}
    return set()


@pytest.mark.parametrize(
    ("role", "expected"),
    [
        ("viewer", {"id": 3, "name": "viewer", "role": "viewer"}),
        ("engineer", {"id": 2, "name": "engineer", "role": "engineer"}),
        ("admin", {"id": 1, "name": "admin", "role": "admin"}),
    ],
)
def test_me_returns_the_caller(client: TestClient, role: str, expected: dict[str, object]) -> None:
    r = client.get("/api/me", headers=bearer(role))
    assert r.status_code == 200
    assert r.json() == {**expected, "organization": {"id": 1, "name": "dev"}}


def test_me_has_no_secrets(client: TestClient) -> None:
    body = client.get("/api/me", headers=bearer("admin")).json()
    assert set(body) == {"id", "name", "role", "organization"}
    assert not all_keys(body) & SECRET_KEYS
    assert "admin-token" not in str(body)


def test_me_needs_a_user_token(client: TestClient) -> None:
    assert client.get("/api/me").status_code == 401
    assert client.get("/api/me", headers=bearer("not-a-token")).status_code == 401
    # The shared dev token is valid for reads but has no user behind it.
    assert client.get("/api/me", headers=bearer(SHARED_TOKEN)).status_code == 401


def test_me_is_a_viewer_route_in_openapi() -> None:
    op = TestClient(app).get("/openapi.json").json()["paths"]["/api/me"]["get"]
    assert op["description"].startswith("Role: viewer.")
    assert set(op["responses"]) >= {"200", "401"}
