"""Real PostGIS + pgvector queries against the compose database (skipped when unreachable)."""

import os
from collections.abc import Iterator

import pytest
from sqlalchemy import Connection, create_engine, text
from sqlalchemy.exc import OperationalError

from app.settings import get_settings


@pytest.fixture
def conn() -> Iterator[Connection]:
    engine = create_engine(get_settings().database_url, connect_args={"connect_timeout": 3})
    try:
        c = engine.connect()
    except OperationalError as err:
        if os.environ.get("QARAU_REQUIRE_DB") == "1":
            pytest.fail(f"database required but unreachable: {err}")
        pytest.skip("database unreachable")
    with c:
        c.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
        c.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        yield c
        c.rollback()
    engine.dispose()


def test_geography_distance(conn: Connection) -> None:
    # Astana -> Almaty, great-circle ~ 972 km (WGS84 ellipsoid, within 5 km of the sphere value)
    d = conn.execute(
        text(
            "SELECT ST_Distance(ST_MakePoint(71.4460, 51.1605)::geography,"
            " ST_MakePoint(76.8512, 43.2220)::geography)"
        )
    ).scalar_one()
    assert 970_000 < d < 976_000


def test_transform_utm42(conn: Connection) -> None:
    # 69E is the central meridian of UTM zone 42: easting 500000; northing = k0 * arc(50N)
    x, y = conn.execute(
        text(
            "SELECT ST_X(g), ST_Y(g) FROM"
            " (SELECT ST_Transform(ST_SetSRID(ST_MakePoint(69, 50), 4326), 32642) AS g) t"
        )
    ).one()
    assert x == pytest.approx(500_000, abs=0.01)
    assert y == pytest.approx(5_538_630.7, abs=1.0)


def test_contains(conn: Connection) -> None:
    sql = (
        "SELECT ST_Contains(ST_MakeEnvelope(0, 0, 10, 10, 4326),"
        " ST_SetSRID(ST_MakePoint(:x, :y), 4326))"
    )
    assert conn.execute(text(sql), {"x": 5, "y": 5}).scalar_one() is True
    assert conn.execute(text(sql), {"x": 11, "y": 5}).scalar_one() is False


def test_pgvector_distance(conn: Connection) -> None:
    d = conn.execute(text("SELECT '[1,2,3]'::vector <-> '[4,6,3]'::vector")).scalar_one()
    assert d == pytest.approx(5.0)
