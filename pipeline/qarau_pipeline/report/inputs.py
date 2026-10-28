"""Everything the PDF report shows, read from the API's database (backend/app/models.py) with
SQLAlchemy Core. `load_report_input` is the one reader used by the CLI and by the backend route.

The finding's status, class and severity are replayed from its append-only decision log
(`replay_decisions`, also used by backend/app/decisions.py); the stored row keeps the proposal.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

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
    area_uncertainty_pct: float | None  # None: not estimated (never invented)
    run_dir: str  # relative to QARAU_RUNS_DIR: where the evidence frame images live
    evidence: tuple[Evidence, ...]
    decisions: tuple[DecisionEntry, ...]  # oldest first
    points: tuple[int, ...] = ()  # indices into the run's bundle points: its map location


@dataclass(frozen=True)
class InspectionInput:
    id: int
    mode: str  # quick_scan | monitoring
    date: datetime  # aware


@dataclass(frozen=True)
class RegionInput:
    """A not-seen map region (COV1): the voxel cell in bundle units and where to look from."""

    key: str
    bounds: tuple[tuple[float, float, float], tuple[float, float, float]]  # min xyz, max xyz
    point_count: int
    nearest_frames: tuple[str, ...]


@dataclass(frozen=True)
class CoverageInput:
    """Coverage of the inspection's latest run with coverage: region counts per state and the
    not-seen regions, most points first."""

    inspected_ok: int
    finding: int
    not_seen: tuple[RegionInput, ...]


def coverage_input(regions: Iterable[Mapping[str, Any]]) -> CoverageInput:
    """From region dicts as regions.json carries them (key `id`) or coverage_region rows (key
    `region_key`; their `id` is the row's): state, bounds, point_count, nearest_frames."""
    regions = list(regions)
    gone = [
        RegionInput(
            key=str(r["region_key"] if "region_key" in r else r["id"]),
            bounds=(_xyz(r["bounds"][0]), _xyz(r["bounds"][1])),
            point_count=int(r["point_count"]),
            nearest_frames=tuple(str(f) for f in r["nearest_frames"] or ()),
        )
        for r in regions
        if r["state"] == "not_seen"
    ]
    gone.sort(key=lambda r: (-r.point_count, r.key))
    return CoverageInput(
        inspected_ok=sum(r["state"] == "inspected_ok" for r in regions),
        finding=sum(r["state"] == "finding" for r in regions),
        not_seen=tuple(gone),
    )


def _xyz(v: Iterable[float]) -> tuple[float, float, float]:
    x, y, z = (float(c) for c in v)
    return x, y, z


@dataclass(frozen=True)
class VersionEntry:
    version: int
    time: datetime  # aware
    author: str | None  # None: no decision yet, so no engineer


@dataclass(frozen=True)
class ReportInput:
    organization: str
    site: str
    asset: str | None  # None: not recorded (no Asset table yet)
    inspection: InspectionInput
    capture_date: datetime  # aware
    report_number: str
    version: int
    engineer: str | None  # the user of the inspection's latest decision; None: no decision yet
    report_time: datetime  # aware; the only clock in the PDF
    findings: tuple[FindingInput, ...]
    versions: tuple[VersionEntry, ...]  # document control, oldest first
    verification_id: str
    coverage: CoverageInput | None = None  # None: coverage not computed (never "all seen")
    run_dirs: tuple[str, ...] = ()  # the inspection's runs by id; the map: the last exported


def verification_id(*parts: object) -> str:
    """A short ID derived from the report's content: same content, same ID."""
    h = hashlib.sha256(repr(parts).encode()).hexdigest()[:16].upper()
    return "-".join(h[i : i + 4] for i in range(0, 16, 4))


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
    sa.column("points", sa.JSON),
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
coverage_region = sa.table(
    "coverage_region", sa.column("id"), sa.column("run_id"), sa.column("region_key"),
    sa.column("state"), sa.column("bounds", sa.JSON), sa.column("point_count"),
    sa.column("nearest_frames", sa.JSON),
)  # fmt: skip


def _aware(t: datetime) -> datetime:
    """SQLite gives naive datetimes; the API stores UTC."""
    return t if t.tzinfo is not None else t.replace(tzinfo=UTC)


def load_report_input(engine: sa.Engine, inspection_id: int, report_time: datetime) -> ReportInput:
    """The report of an inspection as of `report_time`: version 1 of `QV-<year>-<inspection id>`,
    the capture date is the inspection's date. The API's `report` table logs every generation
    (hash, time, who) but does not feed back into the PDF, so the same data and report time
    always give the same bytes.
    ponytail: fixed version 1; number versions from the report table once a report is issued
    (signed off), which needs the sign-off workflow."""
    if report_time.tzinfo is None:
        raise StageError("report time needs a time zone (ISO 8601 with an offset)")
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
        run_dirs = tuple(c.execute(
            sa.select(run.c.run_dir).where(run.c.inspection_id == inspection_id).order_by(run.c.id)
        ).scalars())  # fmt: skip
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
        # ponytail: "latest" = the highest run id, as for the map; order by date when runs get one
        cov_run = c.execute(
            sa.select(sa.func.max(coverage_region.c.run_id))
            .select_from(coverage_region)
            .join(run, run.c.id == coverage_region.c.run_id)
            .where(run.c.inspection_id == inspection_id)
        ).scalar()
        coverage = None if cov_run is None else coverage_input(
            r._mapping for r in c.execute(
                sa.select(coverage_region).where(coverage_region.c.run_id == cov_run)
                .order_by(coverage_region.c.id)
            )
        )  # fmt: skip
    findings = []
    for r in rows:
        proposed = (r.status, r._mapping["class"], r.severity)
        status, class_, severity = replay_decisions(*proposed, decs[r.id])
        findings.append(FindingInput(
            id=r.id, class_=class_, severity=severity, status=status, area_m2=r.area_m2,
            scale_source=r.scale_source, area_uncertainty_pct=None, run_dir=r.run_dir,
            evidence=tuple(obs[r.id]),
            decisions=tuple(decs[r.id]), points=tuple(int(i) for i in r.points or ()),
        ))  # fmt: skip
    last = max((d for f in findings for d in f.decisions), key=lambda d: d.time, default=None)
    engineer = None if last is None else last.user
    insp = InspectionInput(inspection_id, head[2], _aware(head[3]))
    number = f"QV-{insp.date.year}-{inspection_id:04d}"
    found = tuple(findings)
    return ReportInput(
        organization=head[0], site=head[1], asset=None, inspection=insp, capture_date=insp.date,
        report_number=number, version=1, engineer=engineer, report_time=report_time,
        findings=found, versions=(VersionEntry(1, report_time, engineer),),
        verification_id=verification_id(number, 1, report_time.isoformat(), engineer, found,
                                        coverage),
        coverage=coverage, run_dirs=run_dirs,
    )  # fmt: skip
