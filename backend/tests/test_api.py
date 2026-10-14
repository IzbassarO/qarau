import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import health
from app.main import app
from app.settings import get_settings


@pytest.fixture(autouse=True)
def _clean() -> Iterator[None]:
    yield
    app.dependency_overrides.clear()
    get_settings.cache_clear()


@pytest.fixture
def runs_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    web = tmp_path / "r1" / "web"
    web.mkdir(parents=True)
    (web / "manifest.json").write_text(json.dumps({"source": {"kind": "video"}, "stats": {"a": 1}}))
    (web / "points.position.f32").write_bytes(b"\x00\x01\x02\x03")
    (tmp_path / "empty").mkdir()  # no manifest: skipped
    (tmp_path / "secret.txt").write_text("nope")
    monkeypatch.setenv("QARAU_RUNS_DIR", str(tmp_path))
    get_settings.cache_clear()
    return tmp_path


def client(token: str = "") -> TestClient:
    base = get_settings()
    app.dependency_overrides[get_settings] = lambda: base.model_copy(
        update={"qarau_api_token": token}
    )
    return TestClient(app)


def test_health_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(health, "check_db", lambda: "ok")
    monkeypatch.setattr(health, "check_storage", lambda: "ok")
    r = TestClient(app).get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "db": "ok", "storage": "ok"}


def test_health_degraded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(health, "check_db", lambda: "ok")
    monkeypatch.setattr(health, "check_storage", lambda: "error: refused")
    r = TestClient(app).get("/health")
    assert r.status_code == 503
    assert r.json() == {"status": "degraded", "db": "ok", "storage": "error: refused"}


def test_list_manifest_file(runs_dir: Path) -> None:
    c = client()
    assert c.get("/api/runs").json() == [
        {"run_id": "r1", "source": {"kind": "video"}, "stats": {"a": 1}}
    ]
    assert c.get("/api/runs/r1/manifest.json").json()["stats"] == {"a": 1}
    r = c.get("/api/runs/r1/files/web/points.position.f32")
    assert r.status_code == 200
    assert r.content == b"\x00\x01\x02\x03"
    assert r.headers["content-type"] == "application/octet-stream"
    assert r.headers["cache-control"] == "public, max-age=3600"
    assert c.get("/api/runs/r1/files/web/missing.bin").status_code == 404


@pytest.mark.parametrize(
    "url",
    [
        "/api/runs/r1/files/../../secret.txt",
        "/api/runs/r1/files/%2e%2e/%2e%2e/secret.txt",
        "/api/runs/r1/files/..%2f..%2fsecret.txt",
        "/api/runs/r1/files//etc/passwd",
        "/api/runs/%2e%2e/manifest.json",
        "/api/runs/empty/manifest.json",
    ],
)
def test_traversal_rejected(runs_dir: Path, url: str) -> None:
    assert client().get(url).status_code == 404


def test_token_required(runs_dir: Path) -> None:
    c = client(token="s3cret")
    assert c.get("/api/runs").status_code == 401
    assert c.get("/api/runs", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert c.get("/api/runs", headers={"Authorization": "Bearer s3cret"}).status_code == 200
