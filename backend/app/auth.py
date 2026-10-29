"""Bearer-token auth with real roles (ADR 0021) and event guests (ADR 0034).

`Authorization: Bearer <token>`. A token is either the shared read-only dev token
(`QARAU_API_TOKEN`), a guest session (SHA-256 on its `guest` row), or a per-user secret stored as
scrypt(token, per-row salt). Roles form a ladder: guest < viewer < engineer < admin. Missing or
unknown token → 401, role too low → 403. When `QARAU_API_TOKEN` is empty (open dev mode, M1
default) read routes need no token; write routes always need a real user.

A guest passes only routes declared with `require_role(..., guests=True)`; every other route
answers 403. Those routes scope a guest to its event's shared runs (`check_run`,
`check_inspection`) and to its own sandbox rows (`own`). POST /api/auth/guest turns an event
code into a guest session.
"""

import hashlib
import logging
import math
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Lock
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import ColumnElement, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import InstrumentedAttribute, Session

from .db import get_db
from .event import code_sha256
from .models import AuditLog, EventCode, EventCodeRun, Guest, Organization, Role, Run, User, now
from .ratelimit import LIMITER
from .settings import Settings, get_settings

log = logging.getLogger(__name__)
RANK = {Role.guest: -1, Role.viewer: 0, Role.engineer: 1, Role.admin: 2}
UNAUTHORIZED = {"WWW-Authenticate": "Bearer"}


def new_salt() -> str:
    return secrets.token_hex(16)


def hash_token(token: str, salt: str) -> str:
    return hashlib.scrypt(
        token.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1, dklen=32
    ).hex()


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def aware(t: datetime) -> datetime:
    """SQLite gives naive datetimes; we store UTC."""
    return t if t.tzinfo is not None else t.replace(tzinfo=UTC)


@dataclass(frozen=True)
class Identity:
    role: Role
    user_id: int | None = None  # None: shared dev token or open dev mode (read-only)
    guest_id: int | None = None  # set only for a guest (its user is the event's guest user)
    event_code_id: int | None = None


# sha256(token) -> (user id, secret_hash). scrypt costs ~30 ms, so a verified token is remembered
# and re-checked only against the stored hash (a rotated secret misses and triggers a full scan).
# ponytail: linear scan over all users on a miss; add a token prefix index when users > ~100.
_verified: dict[str, tuple[int, str]] = {}


def find_user(db: Session, token: str) -> User | None:
    """The user owning `token`, or None. A database that is down or not migrated also gives
    None (fail closed: 401), with a warning in the log; the shared token never needs the DB.
    An event's guest user never signs in with a user token."""
    key = sha256(token)
    try:
        if (hit := _verified.get(key)) is not None:
            user = db.get(User, hit[0])
            if user is not None and secrets.compare_digest(user.secret_hash, hit[1]):
                return user
        for user in db.scalars(select(User).where(User.role != Role.guest)):
            if secrets.compare_digest(hash_token(token, user.salt), user.secret_hash):
                _verified[key] = (user.id, user.secret_hash)
                return user
    except SQLAlchemyError as e:
        log.warning("user lookup failed, treating token as unknown: %s", e)
        db.rollback()
    return None


def find_guest(db: Session, token: str) -> Identity | None:
    """The guest session behind `token` while its event code has not expired, else None."""
    try:
        row = db.execute(
            select(Guest.id, EventCode.id, EventCode.expires_at, User.id)
            .join(EventCode, EventCode.id == Guest.event_code_id)
            .join(User, (User.org_id == Guest.organization_id) & (User.role == Role.guest))
            .where(Guest.token_sha256 == sha256(token))
        ).first()
    except SQLAlchemyError as e:
        log.warning("guest lookup failed, treating token as unknown: %s", e)
        db.rollback()
        return None
    if row is None or aware(row[2]) <= now():
        return None
    return Identity(Role.guest, row[3], guest_id=row[0], event_code_id=row[1])


def require_role(role: str, guests: bool = False) -> Callable[..., Identity]:
    """Dependency: the caller's Identity if its role is at least `role`, else 401/403. A guest
    passes only when `guests` is true; the route then scopes what the guest sees."""
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
        elif token and (guest := find_guest(db, token)) is not None:
            identity = guest
        elif token and (user := find_user(db, token)) is not None:
            identity = Identity(user.role, user.id)
        if identity is None:
            if shared or need > 0:
                raise HTTPException(401, "invalid or missing token", UNAUTHORIZED)
            identity = Identity(Role.viewer)  # open dev mode: reads are public
        if identity.role == Role.guest and guests:
            return identity
        if RANK[identity.role] < need or identity.role == Role.guest:
            raise HTTPException(403, f"role '{identity.role}' may not use a '{role}' route")
        return identity

    return check


# --- guest scoping ---------------------------------------------------------------------------


def shared_runs(db: Session, who: Identity) -> list[Run]:
    """The real runs shared read-only with the guest's event (`app.event create --runs`)."""
    return list(
        db.scalars(
            select(Run)
            .join(EventCodeRun, EventCodeRun.run_id == Run.id)
            .where(EventCodeRun.event_code_id == who.event_code_id)
            .order_by(Run.id)
        )
    )


def check_run(db: Session, who: Identity, name: str) -> None:
    """404 for a guest unless `name` (a run id or run dir, as in /api/runs/{name}) is shared."""
    if who.role == Role.guest and name not in {
        n for r in shared_runs(db, who) for n in (r.id, r.run_dir)
    }:
        raise HTTPException(404, "run not found")


def check_inspection(db: Session, who: Identity, inspection_id: int) -> None:
    """404 for a guest unless every run of the inspection is shared with its event: the
    inspection's findings, coverage and report then show nothing of an unshared run."""
    if who.role != Role.guest:
        return
    runs = set(db.scalars(select(Run.id).where(Run.inspection_id == inspection_id)))
    if not runs or not runs <= {r.id for r in shared_runs(db, who)}:
        raise HTTPException(404, "inspection not found")


def own(column: InstrumentedAttribute[int | None], who: Identity) -> ColumnElement[bool]:
    """Rows of the caller's sandbox: a guest's own rows; for everyone else rows of no guest."""
    return column.is_(None) if who.guest_id is None else column == who.guest_id


# --- guest sign-in ---------------------------------------------------------------------------

router = APIRouter(prefix="/api/auth")
Db = Annotated[Session, Depends(get_db)]
Cfg = Annotated[Settings, Depends(get_settings)]
# ponytail: max_guests is counted under a process lock; one API process (as the rate limiter).
_sign_in = Lock()


class GuestIn(BaseModel):
    code: str = Field(min_length=1, max_length=64)


class SharedRunOut(BaseModel):
    run_id: str
    run_dir: str  # the {run_id} of /api/runs/{run_id}/manifest.json
    inspection_id: int


class GuestOut(BaseModel):
    guest_id: int
    event: str
    organization: str
    expires_at: datetime
    runs: list[SharedRunOut]


class GuestSession(GuestOut):
    token: str  # shown once; only its SHA-256 is stored


def guest_out(db: Session, who: Identity) -> GuestOut:
    event = db.get(EventCode, who.event_code_id)
    org = db.get(Organization, event.organization_id) if event is not None else None
    if who.guest_id is None or event is None or org is None:
        raise HTTPException(401, "invalid or missing token", UNAUTHORIZED)
    return GuestOut(
        guest_id=who.guest_id,
        event=event.name,
        organization=org.name,
        expires_at=aware(event.expires_at),
        runs=[
            SharedRunOut(run_id=r.id, run_dir=r.run_dir, inspection_id=r.inspection_id)
            for r in shared_runs(db, who)
        ],
    )


def log_code_use(db: Session, request: Request, event: str | None, result: str, ip: str) -> None:
    """One audit row per code use. No user and no guest id: purge never has to delete it."""
    db.add(
        AuditLog(
            user_id=None,
            route=f"{request.method} {request.url.path}",
            object_type="event_code",
            object_id=event,
            action="event_code.use",
            detail={"result": result, "ip": ip},
        )
    )
    db.commit()


@router.post(
    "/guest",
    status_code=201,
    description="Role: public sign-in (no token needed). Exchange an event code for a guest "
    "session: a bearer token (shown once) for a sandbox of your own in the event organization, "
    "and the real runs shared read-only with the event. 401: unknown or expired code; 403: the "
    "event is full; 429 with Retry-After: too many attempts from this IP or with this code. "
    "Every attempt is written to the audit log (action event_code.use).",
    responses={
        401: {"description": "Unknown or expired event code"},
        403: {"description": "The event is full"},
        429: {"description": "Too many attempts; see Retry-After"},
    },
)
def guest_sign_in(body: GuestIn, request: Request, db: Db, settings: Cfg) -> GuestSession:
    ip = request.client.host if request.client is not None else "unknown"
    digest = code_sha256(body.code)
    for key, limit in (
        (f"ip:{ip}", settings.qarau_guest_rate_per_ip),
        (f"code:{digest}", settings.qarau_guest_rate_per_code),
    ):
        if (wait := LIMITER.hit(key, limit)) is not None:
            log_code_use(db, request, None, "rate_limited", ip)
            raise HTTPException(
                429,
                "too many sign-in attempts, try again later",
                {"Retry-After": str(math.ceil(wait))},
            )
    event = db.scalar(select(EventCode).where(EventCode.code_sha256 == digest))
    if event is None:
        log_code_use(db, request, None, "unknown", ip)
        raise HTTPException(401, "unknown event code")
    if aware(event.expires_at) <= now():
        log_code_use(db, request, event.name, "expired", ip)
        raise HTTPException(401, "this event code has expired")
    with _sign_in:
        count = select(func.count()).select_from(Guest).where(Guest.event_code_id == event.id)
        if (db.scalar(count) or 0) >= event.max_guests:
            log_code_use(db, request, event.name, "full", ip)
            raise HTTPException(403, "event is full")
        token = secrets.token_urlsafe(32)
        guest = Guest(
            organization_id=event.organization_id,
            event_code_id=event.id,
            token_sha256=sha256(token),
        )
        db.add(guest)
        db.flush()
        log_code_use(db, request, event.name, "ok", ip)  # commits the guest with its audit row
    who = Identity(Role.guest, guest_id=guest.id, event_code_id=event.id)
    return GuestSession(token=token, **guest_out(db, who).model_dump())


@router.get(
    "/guest",
    description="Role: guest. The signed-in guest's session: guest id, event, organization, "
    "expiry and the shared runs (never the token). Users of every other role get 403.",
)
def read_guest(
    db: Db, who: Annotated[Identity, Depends(require_role("viewer", guests=True))]
) -> GuestOut:
    if who.role != Role.guest:
        raise HTTPException(403, f"role '{who.role}' has no guest session")
    return guest_out(db, who)
