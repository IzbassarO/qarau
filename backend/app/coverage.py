"""Coverage of an inspection's map (COV1-b): the regions the coverage step stored
(app/workflow.py `fill_coverage`) with their state and the share of each state.

GET /api/inspections/{id}/coverage  (viewer) -> CoverageOut

Totals are percentages of the region count, 1 decimal. No stored regions: `computed` is false
and `totals` is empty, never zeros that would read as "nothing found".
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from .auth import check_inspection
from .crud import Db, ViewerOrGuest
from .models import CoverageRegion, CoverageState, Inspection, Role, Run, Site, User

router = APIRouter(prefix="/api")


class BoundsOut(BaseModel):
    min: list[float]  # [x, y, z], bundle units
    max: list[float]


class RegionOut(BaseModel):
    id: str
    state: CoverageState
    frame_count: int
    bounds: BoundsOut


class CoverageOut(BaseModel):
    regions: list[RegionOut]
    totals: dict[CoverageState, float]  # % of the regions per state; empty when not computed
    computed: bool


@router.get(
    "/inspections/{inspection_id}/coverage",
    description="Role: viewer. Coverage regions of the inspection's newest run that has them: "
    "id, state (inspected_ok | finding | not_seen), frame count, bounds (bundle units), and "
    "totals in % of the region count (1 decimal). computed=false and empty totals when coverage "
    "was not computed. 404: unknown inspection, or one of another organization (a guest: one "
    "whose runs are not all shared with its event).",
)
def get_coverage(inspection_id: int, db: Db, who: ViewerOrGuest) -> CoverageOut:
    insp = db.get(Inspection, inspection_id)
    site = db.get(Site, insp.site_id) if insp is not None else None
    user = db.get(User, who.user_id) if who.user_id is not None else None
    # The shared read-only dev token has no organization and reads everything, as elsewhere.
    # A guest reads the shared runs of another organization: check_inspection scopes it.
    if site is None or (user is not None and who.role != Role.guest and user.org_id != site.org_id):
        raise HTTPException(404, "inspection not found")
    check_inspection(db, who, inspection_id)
    run_id = db.scalar(
        select(CoverageRegion.run_id)
        .join(Run, Run.id == CoverageRegion.run_id)
        .where(Run.inspection_id == inspection_id)
        .order_by(Run.created_at.desc(), Run.id.desc())
        .limit(1)
    )
    rows = db.scalars(
        select(CoverageRegion)
        .where(CoverageRegion.run_id == run_id)
        .order_by(CoverageRegion.region_key)
    ).all()
    totals = (
        {st: round(100 * sum(r.state == st for r in rows) / len(rows), 1) for st in CoverageState}
        if rows
        else {}
    )
    return CoverageOut(
        regions=[
            RegionOut(
                id=r.region_key, state=r.state, frame_count=r.frame_count,
                bounds=BoundsOut(min=r.bounds_min, max=r.bounds_max),
            )
            for r in rows
        ],
        totals=totals,
        computed=bool(rows),
    )  # fmt: skip
