"""Manual findings (DEF1-api, ADR 0023). An engineer marks a region of the map; the server
selects the bundle points inside it, measures it and attaches the 3 best evidence frames.

POST /api/inspections/{id}/findings  (engineer) -> 201 FindingOut
GET  /api/inspections/{id}/findings  (viewer)   -> [FindingOut]
GET  /api/findings/{id}              (viewer)   -> FindingOut

Area is in bundle units²; `area_m2` is set only when the inspection has a ScaleReference with
`m_per_unit` in its payload, otherwise it is null and `scale` says "not set".

`status`, `class` and `severity` in every response are derived from the finding's decision log
(app/decisions.py, REV1-api); the `finding` row keeps the values as proposed.
"""

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator
from qarau_pipeline.findings import (
    Bundle,
    evidence_for,
    load_bundle,
    region_area,
    select_indices,
    select_points,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import check_inspection, own
from .crud import Db, EngineerOrGuest, ViewerOrGuest, audit, get_or_404
from .decisions import finding_or_404, state_of
from .models import (
    Finding,
    FindingClass,
    Inspection,
    Observation,
    Run,
    ScaleReference,
    Severity,
)
from .runs import manifest_file
from .settings import Settings, get_settings

router = APIRouter(prefix="/api")
Cfg = Annotated[Settings, Depends(get_settings)]
NOT_SET = "not set"


class FindingIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    class_: FindingClass = Field(alias="class")
    severity: Severity
    comment: str | None = Field(default=None, max_length=2000)
    polygon: list[tuple[float, float, float]] | None = Field(
        default=None, min_length=3, description="Region in map coordinates: >= 3 [x, y, z]"
    )
    point_indices: list[int] | None = Field(
        default=None, min_length=1, description="Region as indices into the bundle's points"
    )

    @model_validator(mode="after")
    def exactly_one_region(self) -> "FindingIn":
        if (self.polygon is None) == (self.point_indices is None):
            raise ValueError("give exactly one of polygon or point_indices")
        return self


class EvidenceOut(BaseModel):
    frame_id: int
    image_url: str
    outline: list[list[float]]  # [[u, v], ...] pixels in the frame image
    confidence: float


class FindingOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    id: int
    inspection_id: int
    run_id: str
    class_: FindingClass = Field(alias="class")
    severity: Severity
    status: str
    comment: str | None
    area_units: float  # bundle units²
    area_m2: float | None  # only with a ScaleReference
    scale: str  # "not set" or "<kind>: <source>" of the ScaleReference used
    point_indices: list[int]
    points: list[list[float]]  # xyz of the selected points, map coordinates
    evidence: list[EvidenceOut]


def exported_run(db: Session, settings: Settings, inspection_id: int) -> Run:
    """The inspection's run that has a viewer bundle; 409 when there is none."""
    # ponytail: several exported runs -> the last by id; pick by request when runs get a date
    runs = db.scalars(select(Run).where(Run.inspection_id == inspection_id).order_by(Run.id))
    ready = [r for r in runs if manifest_file(run_path(settings, r)) is not None]
    if not ready:
        raise HTTPException(
            409, "inspection has no exported run (export/web/manifest.json) to mark findings on"
        )
    return ready[-1]


def run_path(settings: Settings, run: Run) -> Path:
    return Path(settings.qarau_runs_dir) / run.run_dir


def bundle_of(settings: Settings, run: Run) -> Bundle:
    try:
        return load_bundle(run_path(settings, run))
    except (OSError, ValueError, KeyError) as e:
        raise HTTPException(409, f"run {run.id} has no readable viewer bundle: {e}") from e


def scale_of(db: Session, inspection_id: int) -> tuple[float | None, str]:
    """(metres per bundle unit, label) from the inspection's newest usable ScaleReference."""
    refs = db.scalars(
        select(ScaleReference)
        .where(ScaleReference.inspection_id == inspection_id)
        .order_by(ScaleReference.id.desc())
    )
    for ref in refs:
        m = (ref.payload or {}).get("m_per_unit")
        if isinstance(m, int | float) and not isinstance(m, bool) and m > 0:
            return float(m), f"{ref.kind}: {ref.source}"
    return None, NOT_SET


def to_out(db: Session, finding: Finding, run: Run, bundle: Bundle) -> FindingOut:
    idx = [int(i) for i in finding.points]
    obs = db.scalars(
        select(Observation).where(Observation.finding_id == finding.id).order_by(Observation.id)
    )
    status, class_, severity = state_of(db, finding)
    return FindingOut(
        id=finding.id,
        inspection_id=run.inspection_id,
        run_id=run.id,
        class_=FindingClass(class_),
        severity=severity,
        status=status,
        comment=finding.comment,
        area_units=finding.area_units,
        area_m2=finding.area_m2,
        scale=finding.scale_source or NOT_SET,
        point_indices=idx,
        points=bundle.positions[idx].round(4).tolist(),
        evidence=[
            EvidenceOut(
                frame_id=o.frame_id,
                image_url=f"/api/runs/{run.run_dir}/files/{bundle.images[o.frame_id]}",
                outline=o.outline or [],
                confidence=o.confidence,
            )
            for o in obs
        ],
    )


@router.post(
    "/inspections/{inspection_id}/findings",
    status_code=201,
    description="Role: engineer. Also a guest: into its own sandbox, on an inspection whose runs "
    "are all shared with its event (else 404). Mark a finding by hand: a polygon in map "
    "coordinates or a list "
    "of point indices. The server selects the map points inside the region, measures its area "
    "(bundle units²; m² only when the inspection has a ScaleReference, else scale is 'not set') "
    "and stores the 3 best evidence frames with the region outlined in each (confidence 1.0). "
    "422: bad body or a region with no points; 409: the inspection has no exported run.",
)
def create_finding(
    inspection_id: int,
    body: FindingIn,
    db: Db,
    who: EngineerOrGuest,
    request: Request,
    settings: Cfg,
) -> FindingOut:
    get_or_404(db, Inspection, inspection_id)
    check_inspection(db, who, inspection_id)
    finding, run, bundle = mark_finding(db, settings, inspection_id, body, who.guest_id)
    audit(db, who, request, "finding", finding.id, "create")
    db.commit()
    return to_out(db, finding, run, bundle)


def mark_finding(
    db: Session,
    settings: Settings,
    inspection_id: int,
    body: FindingIn,
    guest_id: int | None = None,
) -> tuple[Finding, Run, Bundle]:
    """Select, measure and store a manual finding with its evidence frames (not committed).
    The API route and the dev seed (app/seed.py) both create findings only through this.
    `guest_id`: the finding goes into that guest's sandbox (ADR 0034)."""
    run = exported_run(db, settings, inspection_id)
    bundle = bundle_of(settings, run)
    try:
        if body.polygon is not None:
            sel = select_points(bundle.positions, body.polygon)
        else:
            sel = select_indices(len(bundle.positions), body.point_indices)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    if not len(sel):
        raise HTTPException(422, "the region selects no points of the map")
    area = region_area(bundle.positions[sel])
    m_per_unit, scale = scale_of(db, inspection_id)
    finding = Finding(
        run_id=run.id,
        class_=body.class_.value,
        severity=body.severity,
        comment=body.comment,
        region=body.model_dump(include={"polygon", "point_indices"}, exclude_none=True),
        points=sel.tolist(),
        area_units=area,
        area_m2=None if m_per_unit is None else area * m_per_unit**2,
        scale_source=None if m_per_unit is None else scale,
        guest_id=guest_id,
    )
    db.add(finding)
    db.flush()
    for ev in evidence_for(bundle, sel):
        db.add(
            Observation(
                finding_id=finding.id, frame_id=ev["frame"], outline=ev["outline"], confidence=1.0
            )
        )
    return finding, run, bundle


@router.get(
    "/inspections/{inspection_id}/findings",
    description="Role: viewer. Findings of an inspection with their points and evidence frames. "
    "A guest sees only its own findings, users never see a guest's.",
)
def list_findings(
    inspection_id: int, db: Db, who: ViewerOrGuest, settings: Cfg
) -> list[FindingOut]:
    get_or_404(db, Inspection, inspection_id)
    check_inspection(db, who, inspection_id)
    rows = db.execute(
        select(Finding, Run)
        .join(Run, Finding.run_id == Run.id)
        .where(Run.inspection_id == inspection_id, own(Finding.guest_id, who))
        .order_by(Finding.id)
    ).all()
    bundles: dict[str, Bundle] = {}
    out = []
    for finding, run in rows:
        if run.id not in bundles:
            bundles[run.id] = bundle_of(settings, run)
        out.append(to_out(db, finding, run, bundles[run.id]))
    return out


@router.get(
    "/findings/{finding_id}",
    description="Role: viewer. One finding: class, severity, status, area and scale source, "
    "3D points, evidence frames with outlines and image URLs. 404 for another guest's finding "
    "(and for a guest's finding to every user).",
)
def get_finding(finding_id: int, db: Db, who: ViewerOrGuest, settings: Cfg) -> FindingOut:
    finding = finding_or_404(db, finding_id, who)
    run = db.get(Run, finding.run_id)
    if run is None:
        raise HTTPException(404, "finding not found")
    return to_out(db, finding, run, bundle_of(settings, run))
