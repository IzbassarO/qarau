"""Organizations, sites, inspections, users, audit log. Every operation description starts
with "Role: <role>"; every write inserts an AuditLog row."""

import secrets
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import Identity, hash_token, new_salt, require_role
from .db import get_db
from .models import AuditLog, Inspection, Mode, Organization, Role, Run, Site, User

router = APIRouter(prefix="/api")
Db = Annotated[Session, Depends(get_db)]
Viewer = Annotated[Identity, Depends(require_role("viewer"))]
Engineer = Annotated[Identity, Depends(require_role("engineer"))]
Admin = Annotated[Identity, Depends(require_role("admin"))]


def audit(
    db: Session, who: Identity, request: Request, object_type: str, object_id: object, action: str
) -> None:
    if who.user_id is None:  # the shared token is read-only; never reached for a write
        raise HTTPException(403, "writes need a user token")
    db.add(
        AuditLog(
            user_id=who.user_id,
            route=f"{request.method} {request.url.path}",
            object_type=object_type,
            object_id=str(object_id),
            action=action,
        )
    )


def get_or_404(db: Session, model: type[Organization | Site | Inspection], id_: int) -> object:
    obj = db.get(model, id_)
    if obj is None:
        raise HTTPException(404, f"{model.__tablename__} not found")
    return obj


class Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class OrgIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class OrgOut(Out):
    id: int
    name: str


class SiteIn(BaseModel):
    org_id: int
    name: str = Field(min_length=1, max_length=200)


class SiteOut(Out):
    id: int
    org_id: int
    name: str


class InspectionIn(BaseModel):
    site_id: int
    mode: Mode


class InspectionOut(Out):
    id: int
    site_id: int
    mode: Mode
    status: str
    created_at: datetime


class RunOut(Out):
    id: str
    inspection_id: int
    run_dir: str
    status: str
    progress: float
    current_step: str | None = None
    error: str | None = None


class UserIn(BaseModel):
    org_id: int
    name: str = Field(min_length=1, max_length=100)
    role: Role


class UserOut(Out):
    id: int
    org_id: int
    name: str
    role: Role


class UserCreated(UserOut):
    token: str  # shown once; only its scrypt hash is stored


class AuditOut(Out):
    id: int
    user_id: int
    created_at: datetime
    route: str
    object_type: str
    object_id: str | None
    action: str


# --- organizations ---------------------------------------------------------------------------


@router.get("/organizations", description="Role: viewer. List organizations.")
def list_organizations(db: Db, _: Viewer) -> list[OrgOut]:
    return [
        OrgOut.model_validate(o) for o in db.scalars(select(Organization).order_by(Organization.id))
    ]


@router.post("/organizations", status_code=201, description="Role: admin. Create an organization.")
def create_organization(body: OrgIn, db: Db, who: Admin, request: Request) -> OrgOut:
    if db.scalar(select(Organization).where(Organization.name == body.name)) is not None:
        raise HTTPException(409, "organization name already exists")
    org = Organization(name=body.name)
    db.add(org)
    db.flush()
    audit(db, who, request, "organization", org.id, "create")
    db.commit()
    return OrgOut.model_validate(org)


# --- sites -----------------------------------------------------------------------------------


@router.get("/sites", description="Role: viewer. List sites.")
def list_sites(db: Db, _: Viewer) -> list[SiteOut]:
    return [SiteOut.model_validate(s) for s in db.scalars(select(Site).order_by(Site.id))]


@router.post(
    "/sites", status_code=201, description="Role: admin. Create a site in an organization."
)
def create_site(body: SiteIn, db: Db, who: Admin, request: Request) -> SiteOut:
    get_or_404(db, Organization, body.org_id)
    site = Site(org_id=body.org_id, name=body.name)
    db.add(site)
    db.flush()
    audit(db, who, request, "site", site.id, "create")
    db.commit()
    return SiteOut.model_validate(site)


# --- inspections -----------------------------------------------------------------------------


@router.get("/inspections", description="Role: viewer. List inspections.")
def list_inspections(db: Db, _: Viewer) -> list[InspectionOut]:
    rows = db.scalars(select(Inspection).order_by(Inspection.id))
    return [InspectionOut.model_validate(i) for i in rows]


@router.post(
    "/inspections",
    status_code=201,
    description="Role: engineer. Create an inspection (quick_scan or monitoring) for a site.",
)
def create_inspection(body: InspectionIn, db: Db, who: Engineer, request: Request) -> InspectionOut:
    get_or_404(db, Site, body.site_id)
    insp = Inspection(site_id=body.site_id, mode=body.mode)
    db.add(insp)
    db.flush()
    audit(db, who, request, "inspection", insp.id, "create")
    db.commit()
    return InspectionOut.model_validate(insp)


# GET /inspections/{id} (with the latest run's steps) lives in inspections.py (S1).


@router.get(
    "/inspections/{inspection_id}/runs",
    description="Role: viewer. Pipeline runs of an inspection (status, progress, run dir).",
)
def list_inspection_runs(inspection_id: int, db: Db, _: Viewer) -> list[RunOut]:
    get_or_404(db, Inspection, inspection_id)
    rows = db.scalars(select(Run).where(Run.inspection_id == inspection_id).order_by(Run.id))
    return [RunOut.model_validate(r) for r in rows]


# --- users -----------------------------------------------------------------------------------


@router.get("/users", description="Role: admin. List users (never their secrets).")
def list_users(db: Db, _: Admin) -> list[UserOut]:
    return [UserOut.model_validate(u) for u in db.scalars(select(User).order_by(User.id))]


@router.post(
    "/users",
    status_code=201,
    description="Role: admin. Create a user; the response carries its bearer token exactly once.",
)
def create_user(body: UserIn, db: Db, who: Admin, request: Request) -> UserCreated:
    get_or_404(db, Organization, body.org_id)
    if db.scalar(select(User).where(User.org_id == body.org_id, User.name == body.name)):
        raise HTTPException(409, "user name already exists in this organization")
    token = secrets.token_urlsafe(32)
    salt = new_salt()
    user = User(
        org_id=body.org_id,
        name=body.name,
        role=body.role,
        salt=salt,
        secret_hash=hash_token(token, salt),
    )
    db.add(user)
    db.flush()
    audit(db, who, request, "user", user.id, "create")
    db.commit()
    return UserCreated(id=user.id, org_id=user.org_id, name=user.name, role=user.role, token=token)


# --- append-only tables ----------------------------------------------------------------------


@router.get("/audit-log", description="Role: admin. The audit log, newest first.")
def list_audit_log(db: Db, _: Admin) -> list[AuditOut]:
    rows = db.scalars(select(AuditLog).order_by(AuditLog.id.desc()))
    return [AuditOut.model_validate(a) for a in rows]


def decision_immutable(decision_id: int, _: Viewer) -> None:
    raise HTTPException(405, "decisions are append-only")


def audit_log_immutable(entry_id: int, _: Viewer) -> None:
    raise HTTPException(405, "the audit log is append-only")


# Registered explicitly so the refusal is visible in OpenAPI and tested (one operation id per
# method; a single multi-method route would share one id).
for _method in ("PUT", "PATCH", "DELETE"):
    router.add_api_route(
        "/decisions/{decision_id}",
        decision_immutable,
        methods=[_method],
        status_code=405,
        description="Role: viewer. Decisions are append-only: always 405 (the DB refuses too)",
    )
    router.add_api_route(
        "/audit-log/{entry_id}",
        audit_log_immutable,
        methods=[_method],
        status_code=405,
        description="Role: viewer. The audit log is append-only: always 405 (the DB refuses too)",
    )
