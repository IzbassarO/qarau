"""Everything the PDF report shows, read from the API's database (backend/app/models.py) with
SQLAlchemy Core. `load_report_input` is the one reader used by the CLI and by the backend route.

The finding's status, class and severity are replayed from its append-only decision log
(`replay_decisions`, also used by backend/app/decisions.py); the stored row keeps the proposal.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

import sqlalchemy as sa

from ..interfaces import StageError


@dataclass(frozen=True)
class Evidence:
    frame_id: int
    outline: tuple[tuple[float, float], ...]  # pixels in the frame image


@dataclass(frozen=True)
class DecisionEntry:
    user: str
    verdict: str  # confirm | reject | reclass
    comment: str | None
    new_class: str | None  # reclass only
    new_severity: str | None  # reclass only
    time: datetime  # aware


@dataclass(frozen=True)
class FindingInput:
    id: int
    class_: str
    severity: str  # critical | watch
    status: str  # proposed | confirmed | rejected
    area_m2: float | None  # only with a scale reference
    scale_source: str | None  # None: scale not set
    run_dir: str  # relative to QARAU_RUNS_DIR: where the evidence frame images live
    evidence: tuple[Evidence, ...]
    decisions: tuple[DecisionEntry, ...]  # oldest first


@dataclass(frozen=True)
class InspectionInput:
    id: int
    mode: str  # quick_scan | monitoring
    date: datetime  # aware


@dataclass(frozen=True)
class ReportInput:
    organization: str
    site: str
    inspection: InspectionInput
    engineer: str | None  # the user of the inspection's latest decision; None: no decision yet
    findings: tuple[FindingInput, ...]
    coverage: None = None  # not-seen areas; COV1 fills it, until then "coverage not computed"


class _Verdict(Protocol):
    @property
    def verdict(self) -> str: ...
    @property
    def new_class(self) -> str | None: ...
    @property
    def new_severity(self) -> str | None: ...


def replay_decisions(
    status: str, class_: str, severity: str, decisions: Iterable[_Verdict]
) -> tuple[str, str, str]:
    """(status, class, severity) after replaying `decisions` oldest first. The status is that of
    the latest decision: reject -> rejected; confirm and reclass -> confirmed. A reclass sets the
    class and/or severity it carries (a field it does not carry keeps its previous value) and
    later decisions keep them. No decision: the proposed values."""
    for d in decisions:
        status = "rejected" if d.verdict == "reject" else "confirmed"
        if d.verdict == "reclass":
            class_ = d.new_class or class_
            severity = d.new_severity or severity
    return status, class_, severity


# The columns the report reads; the tables themselves belong to backend/ (Alembic migrations).
_tz = sa.DateTime(timezone=True)
organization = sa.table("organization", sa.column("id"), sa.column("name"))
site = sa.table("site", sa.column("id"), sa.column("org_id"), sa.column("name"))
inspection = sa.table(
    "inspection", sa.column("id"), sa.column("site_id"), sa.column("mode"),
    sa.column("created_at", _tz),
)  # fmt: skip
run = sa.table("run", sa.column("id"), sa.column("inspection_id"), sa.column("run_dir"))
finding = sa.table(
    "finding", sa.column("id"), sa.column("run_id"), sa.column("class"), sa.column("severity"),
    sa.column("status"), sa.column("area_m2", sa.Float), sa.column("scale_source"),
)  # fmt: skip
observation = sa.table(
    "observation", sa.column("id"), sa.column("finding_id"), sa.column("frame_id"),
    sa.column("outline", sa.JSON),
)  # fmt: skip
decision = sa.table(
    "decision", sa.column("id"), sa.column("finding_id"), sa.column("user_id"),
    sa.column("verdict"), sa.column("comment"), sa.column("new_class"),
    sa.column("new_severity"), sa.column("created_at", _tz),
)  # fmt: skip
user = sa.table("user", sa.column("id"), sa.column("name"))


def _aware(t: datetime) -> datetime:
    """SQLite gives naive datetimes; the API stores UTC."""
    return t if t.tzinfo is not None else t.replace(tzinfo=UTC)


def load_report_input(engine: sa.Engine, inspection_id: int) -> ReportInput:
    with engine.connect() as c:
        head = c.execute(
            sa.select(organization.c.name, site.c.name, inspection.c.mode, inspection.c.created_at)
            .select_from(inspection)
            .join(site, site.c.id == inspection.c.site_id)
            .join(organization, organization.c.id == site.c.org_id)
            .where(inspection.c.id == inspection_id)
        ).first()
        if head is None:
            raise StageError(f"inspection {inspection_id} not found")
        rows = c.execute(
            sa.select(finding, run.c.run_dir)
            .join(run, run.c.id == finding.c.run_id)
            .where(run.c.inspection_id == inspection_id)
            .order_by(finding.c.id)
        ).all()
        ids = [r.id for r in rows]
        obs: dict[int, list[Evidence]] = {i: [] for i in ids}
        for o in c.execute(
            sa.select(observation)
            .where(observation.c.finding_id.in_(ids))
            .order_by(observation.c.id)
        ):
            outline = tuple((float(u), float(v)) for u, v in (o.outline or []))
            obs[o.finding_id].append(Evidence(int(o.frame_id), outline))
        decs: dict[int, list[DecisionEntry]] = {i: [] for i in ids}
        for d in c.execute(
            sa.select(decision, user.c.name.label("user_name"))
            .join(user, user.c.id == decision.c.user_id)
            .where(decision.c.finding_id.in_(ids))
            .order_by(decision.c.created_at, decision.c.id)
        ):
            entry = DecisionEntry(
                user=d.user_name, verdict=d.verdict, comment=d.comment, new_class=d.new_class,
                new_severity=d.new_severity, time=_aware(d.created_at),
            )  # fmt: skip
            decs[d.finding_id].append(entry)
    findings = []
    for r in rows:
        proposed = (r.status, r._mapping["class"], r.severity)
        status, class_, severity = replay_decisions(*proposed, decs[r.id])
        findings.append(FindingInput(
            id=r.id, class_=class_, severity=severity, status=status, area_m2=r.area_m2,
            scale_source=r.scale_source, run_dir=r.run_dir, evidence=tuple(obs[r.id]),
            decisions=tuple(decs[r.id]),
        ))  # fmt: skip
    last = max((d for f in findings for d in f.decisions), key=lambda d: d.time, default=None)
    return ReportInput(
        organization=head[0],
        site=head[1],
        inspection=InspectionInput(inspection_id, head[2], _aware(head[3])),
        engineer=None if last is None else last.user,
        findings=tuple(findings),
    )
