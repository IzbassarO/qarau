"""SQLAlchemy 2 models. Schema changes only via Alembic migrations (backend/alembic/).

Portable types only (sa.JSON, non-native enums), so the same models run on Postgres and on the
SQLite file the tests use. `decision` and `audit_log` are append-only: the migration adds
triggers that reject UPDATE and DELETE.
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


def now() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return uuid.uuid4().hex


def enum_type(e: type[enum.StrEnum]) -> sa.Enum:
    return sa.Enum(e, native_enum=False, length=16)


class Organization(Base):
    __tablename__ = "organization"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(sa.String(200), unique=True)


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
    The worker (app/workflow.py) writes status, current_step, progress and error after every
    step: status uploading | uploaded | queued | running | done | failed."""

    __tablename__ = "run"
    id: Mapped[str] = mapped_column(sa.String(32), primary_key=True, default=new_id)
    inspection_id: Mapped[int] = mapped_column(sa.ForeignKey("inspection.id"))
    run_dir: Mapped[str] = mapped_column(sa.String(500))  # relative to QARAU_RUNS_DIR
    status: Mapped[str] = mapped_column(sa.String(32), default="created")
    progress: Mapped[float] = mapped_column(sa.Float, default=0.0)  # finished steps / total, %
    current_step: Mapped[str | None] = mapped_column(sa.String(32), nullable=True)
    error: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=now, server_default=sa.text("CURRENT_TIMESTAMP")
    )


class RunStep(Base):
    """One workflow step of a Run, mirrored from <run>/<step>/step.json by the worker.
    status: pending | running | done | failed (the shape web/src/lib/processing.ts parses)."""

    __tablename__ = "run_step"
    __table_args__ = (sa.UniqueConstraint("run_id", "name"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(sa.ForeignKey("run.id"))
    name: Mapped[str] = mapped_column(sa.String(32))
    status: Mapped[str] = mapped_column(sa.String(16), default="pending")
    started_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    duration_s: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    error: Mapped[str | None] = mapped_column(sa.Text, nullable=True)


class Finding(Base):
    __tablename__ = "finding"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(sa.ForeignKey("run.id"))
    class_: Mapped[str] = mapped_column("class", sa.String(64))
    severity: Mapped[Severity] = mapped_column(enum_type(Severity))
    status: Mapped[str] = mapped_column(sa.String(32), default="proposed")
    area_m2: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    area_units: Mapped[str] = mapped_column(sa.String(16), default="rel")
    points: Mapped[Any] = mapped_column(sa.JSON, default=list)


class Observation(Base):
    """A Finding seen in one frame."""

    __tablename__ = "observation"
    id: Mapped[int] = mapped_column(primary_key=True)
    finding_id: Mapped[int] = mapped_column(sa.ForeignKey("finding.id"))
    frame_id: Mapped[int] = mapped_column(sa.Integer)
    mask_path: Mapped[str] = mapped_column(sa.String(500))
    confidence: Mapped[float] = mapped_column(sa.Float)


class Decision(Base):
    """Append-only (trigger in migration 0001). Our future training data: never edited."""

    __tablename__ = "decision"
    id: Mapped[int] = mapped_column(primary_key=True)
    finding_id: Mapped[int] = mapped_column(sa.ForeignKey("finding.id"))
    user_id: Mapped[int] = mapped_column(sa.ForeignKey("user.id"))
    verdict: Mapped[Verdict] = mapped_column(enum_type(Verdict))
    comment: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=now)


class ScaleReference(Base):
    __tablename__ = "scale_reference"
    id: Mapped[int] = mapped_column(primary_key=True)
    inspection_id: Mapped[int] = mapped_column(sa.ForeignKey("inspection.id"))
    kind: Mapped[str] = mapped_column(sa.String(32))  # gps | reference_object | manual
    payload: Mapped[Any] = mapped_column(sa.JSON)
    source: Mapped[str] = mapped_column(sa.String(100))


class AuditLog(Base):
    """Append-only (trigger in migration 0001). One row per write request."""

    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(sa.ForeignKey("user.id"))
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=now)
    route: Mapped[str] = mapped_column(sa.String(200))  # "POST /api/sites"
    object_type: Mapped[str] = mapped_column(sa.String(50))
    object_id: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    action: Mapped[str] = mapped_column(sa.String(50))


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
