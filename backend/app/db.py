from collections.abc import Iterator
from functools import lru_cache
from typing import Annotated, Any

from fastapi import Depends
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session

from .settings import Settings, get_settings


def _sqlite_fks(conn: Any, _record: Any) -> None:
    conn.execute("PRAGMA foreign_keys=ON")


@lru_cache
def engine_for(url: str) -> Engine:
    if url.startswith("sqlite"):
        engine = create_engine(url, connect_args={"check_same_thread": False})
        event.listen(engine, "connect", _sqlite_fks)
        return engine
    return create_engine(url)


def get_db(settings: Annotated[Settings, Depends(get_settings)]) -> Iterator[Session]:
    with Session(engine_for(settings.database_url)) as session:
        yield session
