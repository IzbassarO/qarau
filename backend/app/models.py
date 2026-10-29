"""SQLAlchemy 2 models. Schema changes only via Alembic migrations (backend/alembic/).

Portable types only (sa.JSON, non-native enums), so the same models run on Postgres and on the
SQLite file the tests use. `decision` and `audit_log` are append-only: the migration adds
triggers that reject UPDATE and DELETE (since 0007 a guest's decision in an event organization
may be deleted, for `app.event purge`; ADR 0034).
"""

import enum
import uuid
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Role(enum.StrEnum):
    admin = "admin"
    engineer = "engineer"
    viewer = "viewer"
    guest = "guest"  # an event organization's guest user (ADR 0034); never has a user token


class Mode(enum.StrEnum):
    quick_scan = "quick_scan"
    monitoring = "monitoring"


class Severity(enum.StrEnum):
    critical = "critical"
    watch = "watch"


class Verdict(enum.StrEnum):
    confirm = "confirm"
    reject = "reject"
    reclass = "reclass"


class FindingClass(enum.StrEnum):
    """API vocabulary for `finding.class` (stored as a plain string)."""

    corrosion = "corrosion"
    crack = "crack"
    leak = "leak"
    deformation = "deformation"
    coating = "coating"
    other = "other"


def now() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return uuid.uuid4().hex


def enum_type(e: type[enum.StrEnum]) -> sa.Enum:
    return sa.Enum(e, native_enum=False, length=16)


class Organization(Base):
    """`is_event` marks an organization made by `python -m app.event create` (guest access,
    ADR 0034). Only that CLI sets it, at INSERT; a trigger (migration 0007) rejects any change."""

    __tablename__ = "organization"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(sa.String(200), unique=True)
    is_event: Mapped[bool] = mapped_column(sa.Boolean, default=False, server_default=sa.false())


class User(Base):
    __tablename__ = "user"
    __table_args__ = (sa.UniqueConstraint("org_id", "name"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(sa.ForeignKey("organization.id"))
    name: Mapped[str] = mapped_column(sa.String(100))
    role: Mapped[Role] = mapped_column(enum_type(Role))
    secret_hash: Mapped[str] = mapped_column(sa.String(64))  # scrypt(token, salt), hex
    salt: Mapped[str] = mapped_column(sa.String(32))  # per-row, hex


class Site(Base):
    __tablename__ = "site"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(sa.ForeignKey("organization.id"))
    name: Mapped[str] = mapped_column(sa.String(200))


class Inspection(Base):
    __tablename__ = "inspection"
    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[int] = mapped_column(sa.ForeignKey("site.id"))
    mode: Mapped[Mode] = mapped_column(enum_type(Mode))
    status: Mapped[str] = mapped_column(sa.String(32), default="created")
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=now)


class Run(Base):
    """One pipeline run of an inspection. `id` is also the directory name under QARAU_RUNS_DIR.
    The worker (app/workflow.py) writes status, current_step, progress, error and the step list
    after every step: status uploading | uploaded | queued | running | done | failed.
    `steps` is a JSON list of {name, status (pending | running | done | failed), started_at,
    finished_at (ISO 8601, UTC), duration_s, error}, in workflow order (migration v0002)."""

    __tablename__ = "run"
    id: Mapped[str] = mapped_column(sa.String(32), primary_key=True, default=new_id)
    inspection_id: Mapped[int] = mapped_column(sa.ForeignKey("inspection.id"))
    run_dir: Mapped[str] = mapped_column(sa.String(500))  # relative to QARAU_RUNS_DIR
    status: Mapped[str] = mapped_column(sa.String(32), default="created")
    progress: Mapped[int] = mapped_column(sa.Integer, default=0)  # finished steps / total, 0-100
    current_step: Mapped[str | None] = mapped_column(sa.String(32), nullable=True)
    error: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    rq_job_id: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    steps: Mapped[Any] = mapped_column(sa.JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=now, server_default=sa.text("CURRENT_TIMESTAMP")
    )


class Finding(Base):
    """A defect region on a run's map (manual marking: ADR 0023). Area is measured in bundle
    units²; `area_m2` is filled only from a ScaleReference, named in `scale_source`."""

    __tablename__ = "finding"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(sa.ForeignKey("run.id"))
    class_: Mapped[str] = mapped_column("class", sa.String(64))
    severity: Mapped[Severity] = mapped_column(enum_type(Severity))
    status: Mapped[str] = mapped_column(sa.String(32), default="proposed")
    comment: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    region: Mapped[Any | None] = mapped_column(sa.JSON, nullable=True)  # as marked: polygon | idx
    area_units: Mapped[float] = mapped_column(sa.Float, default=0.0)  # bundle units²
    area_m2: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    scale_source: Mapped[str | None] = mapped_column(sa.String(100), nullable=True)
    points: Mapped[Any] = mapped_column(sa.JSON, default=list)  # bundle point indices
    guest_id: Mapped[int | None] = mapped_column(sa.ForeignKey("guest.id"), nullable=True)


class CoverageState(enum.StrEnum):
    inspected_ok = "inspected_ok"
    finding = "finding"
    not_seen = "not_seen"


class CoverageRegion(Base):
    """One map region of a run's coverage (COV1-b, migration 0006): a row per region of
    <run>/coverage/regions.json, replaced as a whole when the coverage step runs again
    (app/workflow.py). Bounds are [x, y, z] in bundle units."""

    __tablename__ = "coverage_region"
    __table_args__ = (
        sa.UniqueConstraint("run_id", "region_key"),
        sa.CheckConstraint(
            "state IN ('inspected_ok', 'finding', 'not_seen')", name="ck_coverage_region_state"
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(sa.ForeignKey("run.id"), index=True)
    region_key: Mapped[str] = mapped_column(sa.String(64))  # regions.json id, "i_j_k"
    state: Mapped[CoverageState] = mapped_column(enum_type(CoverageState))
    frame_count: Mapped[int] = mapped_column(sa.Integer)
    bounds_min: Mapped[Any] = mapped_column(sa.JSON)
    bounds_max: Mapped[Any] = mapped_column(sa.JSON)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=now)


class Observation(Base):
    """A Finding seen in one frame: a raster mask (model output) or an outline polygon in
    pixels (manual region projected into the frame)."""

    __tablename__ = "observation"
    id: Mapped[int] = mapped_column(primary_key=True)
    finding_id: Mapped[int] = mapped_column(sa.ForeignKey("finding.id"))
    frame_id: Mapped[int] = mapped_column(sa.Integer)
    mask_path: Mapped[str | None] = mapped_column(sa.String(500), nullable=True)
    outline: Mapped[Any | None] = mapped_column(sa.JSON, nullable=True)  # [[u, v], ...]
    confidence: Mapped[float] = mapped_column(sa.Float)


class Decision(Base):
    """Append-only (trigger in migration 0001). Our future training data: never edited.
    `new_class` / `new_severity` are set by a `reclass` verdict only (migration 0004); the
    finding's effective state is replayed from its decisions (app/decisions.py)."""

    __tablename__ = "decision"
    id: Mapped[int] = mapped_column(primary_key=True)
    finding_id: Mapped[int] = mapped_column(sa.ForeignKey("finding.id"))
    user_id: Mapped[int] = mapped_column(sa.ForeignKey("user.id"))
    verdict: Mapped[Verdict] = mapped_column(enum_type(Verdict))
    comment: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    new_class: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    new_severity: Mapped[Severity | None] = mapped_column(enum_type(Severity), nullable=True)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=now)
    guest_id: Mapped[int | None] = mapped_column(sa.ForeignKey("guest.id"), nullable=True)


class ScaleReference(Base):
    __tablename__ = "scale_reference"
    id: Mapped[int] = mapped_column(primary_key=True)
    inspection_id: Mapped[int] = mapped_column(sa.ForeignKey("inspection.id"))
    kind: Mapped[str] = mapped_column(sa.String(32))  # gps | reference_object | manual
    payload: Mapped[Any] = mapped_column(sa.JSON)
    source: Mapped[str] = mapped_column(sa.String(100))


class Report(Base):
    """One generated PDF inspection report (RPT1, migration 0005). `generated_at` is the report
    time printed in the PDF; the same data, language and time give the same `sha256`.
    `created_by` is null for the shared read-only token."""

    __tablename__ = "report"
    id: Mapped[int] = mapped_column(primary_key=True)
    inspection_id: Mapped[int] = mapped_column(sa.ForeignKey("inspection.id"), index=True)
    lang: Mapped[str] = mapped_column(sa.String(2))
    sha256: Mapped[str] = mapped_column(sa.CHAR(64))
    generated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    report_number: Mapped[str] = mapped_column(sa.String(32))
    version: Mapped[int] = mapped_column(sa.Integer)
    created_by: Mapped[int | None] = mapped_column(sa.ForeignKey("user.id"), nullable=True)
    guest_id: Mapped[int | None] = mapped_column(sa.ForeignKey("guest.id"), nullable=True)


class EventCode(Base):
    """A guest access code of an event organization (migration 0007). Only the SHA-256 of the
    code is stored; the code itself is printed once by `python -m app.event create`."""

    __tablename__ = "event_code"
    __table_args__ = (sa.UniqueConstraint("id", "organization_id"),)  # target of guest's FK
    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(sa.ForeignKey("organization.id"))
    name: Mapped[str] = mapped_column(sa.String(64), unique=True)
    code_sha256: Mapped[str] = mapped_column(sa.CHAR(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    max_guests: Mapped[int] = mapped_column(sa.Integer)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=now)


class EventCodeRun(Base):
    """A real processed run shared read-only with the guests of an event code."""

    __tablename__ = "event_code_run"
    event_code_id: Mapped[int] = mapped_column(sa.ForeignKey("event_code.id"), primary_key=True)
    run_id: Mapped[str] = mapped_column(sa.ForeignKey("run.id"), primary_key=True)


class Guest(Base):
    """One guest's sandbox in an event organization: the findings, decisions and reports the
    guest makes carry its id. The database refuses a guest outside an event organization."""

    __tablename__ = "guest"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["event_code_id", "organization_id"], ["event_code.id", "event_code.organization_id"]
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(sa.ForeignKey("organization.id"))
    event_code_id: Mapped[int] = mapped_column(sa.Integer)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=now)
    # The guest's session (migration 0008): SHA-256 of its bearer token, valid until the code
    # expires. Purge deletes the row and with it the session.
    token_sha256: Mapped[str | None] = mapped_column(sa.CHAR(64), unique=True, nullable=True)


class AuditLog(Base):
    """Append-only (trigger in migration 0001). One row per write request. `user_id` is null for
    an anonymous request (a guest code use, migration 0008); `detail` holds its result and IP."""

    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(sa.ForeignKey("user.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=now)
    route: Mapped[str] = mapped_column(sa.String(200))  # "POST /api/sites"
    object_type: Mapped[str] = mapped_column(sa.String(50))
    object_id: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    action: Mapped[str] = mapped_column(sa.String(50))
    detail: Mapped[Any | None] = mapped_column(sa.JSON, nullable=True)


class VideoUpload(Base):
    """Resumable upload of one video into a Run's directory (protocol: docs/architecture.md)."""

    __tablename__ = "video_upload"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(sa.ForeignKey("run.id"))
    filename: Mapped[str] = mapped_column(sa.String(255))
    ext: Mapped[str] = mapped_column(sa.String(8))
    size: Mapped[int] = mapped_column(sa.BigInteger)
    next_offset: Mapped[int] = mapped_column(sa.BigInteger, default=0)
    sha256: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    status: Mapped[str] = mapped_column(sa.String(16), default="uploading")  # uploading | complete
