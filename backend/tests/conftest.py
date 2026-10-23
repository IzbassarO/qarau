"""Fresh database per test: SQLite file by default, Postgres when QARAU_TEST_DATABASE_URL is set.

Schema comes from `alembic upgrade head` (never from metadata.create_all), so the migration is
what the tests exercise. Users admin / engineer / viewer are seeded with literal tokens.
"""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from alembic import command
from app import auth
from app.db import engine_for
from app.main import app
from app.models import Base, Inspection, Mode, Role, Site
from app.seed import seed_dev_users
from app.settings import get_settings

BACKEND = Path(__file__).resolve().parents[1]
TOKENS = {Role.admin: "admin-token", Role.engineer: "engineer-token", Role.viewer: "viewer-token"}
SHARED_TOKEN = "shared-dev-token"


def alembic_config(url: str) -> Config:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def drop_everything(url: str) -> None:
    engine = engine_for(url)
    Base.metadata.drop_all(engine)
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
        if engine.dialect.name == "postgresql":
            conn.execute(text("DROP FUNCTION IF EXISTS qarau_append_only() CASCADE"))


@pytest.fixture
def db_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    url = os.environ.get("QARAU_TEST_DATABASE_URL") or f"sqlite:///{tmp_path / 'test.sqlite'}"
    monkeypatch.setenv("QARAU_DATABASE_URL", url)
    monkeypatch.setenv("QARAU_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("QARAU_API_TOKEN", SHARED_TOKEN)
    get_settings.cache_clear()
    engine_for.cache_clear()
    auth._verified.clear()
    drop_everything(url)
    command.upgrade(alembic_config(url), "head")
    yield url
    app.dependency_overrides.clear()
    get_settings.cache_clear()
    engine_for(url).dispose()
    engine_for.cache_clear()


@pytest.fixture
def runs_root(db_url: str) -> Path:
    return Path(get_settings().qarau_runs_dir)


@pytest.fixture
def client(db_url: str) -> TestClient:
    """Seeded: org 1 "dev" with users 1 admin, 2 engineer, 3 viewer; site 1; inspection 1."""
    with Session(engine_for(db_url)) as db:
        org = seed_dev_users(db, TOKENS)
        site = Site(org_id=org.id, name="Tank farm 7")
        db.add(site)
        db.flush()
        db.add(Inspection(site_id=site.id, mode=Mode.quick_scan))
        db.commit()
    return TestClient(app)


def bearer(role: Role | str) -> dict[str, str]:
    token = TOKENS[Role(role)] if role in Role.__members__ else str(role)
    return {"Authorization": f"Bearer {token}"}
