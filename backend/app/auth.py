"""Bearer-token auth with real roles (ADR 0021).

`Authorization: Bearer <token>`. A token is either the shared read-only dev token
(`QARAU_API_TOKEN`) or a per-user secret stored as scrypt(token, per-row salt). Roles form a
ladder: viewer < engineer < admin. Missing/unknown token → 401, role too low → 403.
When `QARAU_API_TOKEN` is empty (open dev mode, M1 default) read routes need no token; write
routes always need a real user.
"""

import hashlib
import logging
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from .db import get_db
from .models import Role, User
from .settings import Settings, get_settings

log = logging.getLogger(__name__)
RANK = {Role.viewer: 0, Role.engineer: 1, Role.admin: 2}
UNAUTHORIZED = {"WWW-Authenticate": "Bearer"}


def new_salt() -> str:
    return secrets.token_hex(16)


def hash_token(token: str, salt: str) -> str:
    return hashlib.scrypt(
        token.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1, dklen=32
    ).hex()


@dataclass(frozen=True)
class Identity:
    role: Role
    user_id: int | None = None  # None: shared dev token or open dev mode (read-only)


# sha256(token) -> (user id, secret_hash). scrypt costs ~30 ms, so a verified token is remembered
# and re-checked only against the stored hash (a rotated secret misses and triggers a full scan).
# ponytail: linear scan over all users on a miss; add a token prefix index when users > ~100.
_verified: dict[str, tuple[int, str]] = {}


def find_user(db: Session, token: str) -> User | None:
    """The user owning `token`, or None. A database that is down or not migrated also gives
    None (fail closed: 401), with a warning in the log; the shared token never needs the DB."""
    key = hashlib.sha256(token.encode()).hexdigest()
    try:
        if (hit := _verified.get(key)) is not None:
            user = db.get(User, hit[0])
            if user is not None and secrets.compare_digest(user.secret_hash, hit[1]):
                return user
        for user in db.scalars(select(User)):
            if secrets.compare_digest(hash_token(token, user.salt), user.secret_hash):
                _verified[key] = (user.id, user.secret_hash)
                return user
    except SQLAlchemyError as e:
        log.warning("user lookup failed, treating token as unknown: %s", e)
        db.rollback()
    return None


def require_role(role: str) -> Callable[..., Identity]:
    """Dependency: the caller's Identity if its role is at least `role`, else 401/403."""
    need = RANK[Role(role)]

    def check(
        settings: Annotated[Settings, Depends(get_settings)],
        db: Annotated[Session, Depends(get_db)],
        authorization: Annotated[str, Header()] = "",
    ) -> Identity:
        shared = settings.qarau_api_token
        token = authorization.removeprefix("Bearer ") if authorization.startswith("Bearer ") else ""
        identity: Identity | None = None
        if token and shared and secrets.compare_digest(token.encode(), shared.encode()):
            identity = Identity(Role.viewer)
        elif token and (user := find_user(db, token)) is not None:
            identity = Identity(user.role, user.id)
        if identity is None:
            if shared or need > 0:
                raise HTTPException(401, "invalid or missing token", UNAUTHORIZED)
            identity = Identity(Role.viewer)  # open dev mode: reads are public
        if RANK[identity.role] < need:
            raise HTTPException(403, f"role '{identity.role}' may not use a '{role}' route")
        return identity

    return check
