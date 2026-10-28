"""Coverage of an inspection (COV1, ADR 0002): every region of the map is inspected_ok, finding
or not_seen.

GET /api/inspections/{id}/coverage  (viewer) -> CoverageOut

The rows come from the pipeline's <run>/coverage/regions.json, imported by the worker after the
coverage step (`import_regions`: delete + insert per run, so a rerun replaces them). The route
serves the inspection's latest run with coverage; without one it says `not_computed` in plain
words and lists no region, so nothing reads as "all seen".
"""

import json
from pathlib import Path
from typing import Literal, cast

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .crud import Db, Viewer, get_or_404
from .models import CoverageRegion, Inspection, Run

router = APIRouter(prefix="/api")
State = Literal["inspected_ok", "finding", "not_seen"]
STATES: tuple[State, ...] = ("inspected_ok", "finding", "not_seen")
NOT_COMPUTED = (
    "Coverage has not been computed for this inspection yet, so no part of the map is known to "
    "be inspected. Treat the whole map as not inspected."
)


class RegionOut(BaseModel):
    id: str  # voxel key "ix_iy_iz"
    state: State
    bounds: list[list[float]]  # [[min x, y, z], [max x, y, z]], bundle units
    frame_count: int  # usable frames that see the region
    point_count: int
    nearest_frames: list[str]  # frame image names, nearest camera first


class StateTotal(BaseModel):
    count: int
    percent: float  # of the regions, one decimal


class CoverageOut(BaseModel):
    inspection_id: int
    status: Literal["computed", "not_computed"]
    message: str | None  # plain words when not computed
    run_id: str | None
    totals: dict[State, StateTotal] | None  # None when not computed
    regions: list[RegionOut]


def import_regions(db: Session, run_id: str, run_dir: Path) -> None:
    """<run_dir>/coverage/regions.json -> the run's coverage_region rows, replacing the old ones.
    The caller commits."""
    doc = json.loads((run_dir / "coverage" / "regions.json").read_text())
    db.execute(delete(CoverageRegion).where(CoverageRegion.run_id == run_id))
    db.add_all(
        CoverageRegion(
            run_id=run_id,
            region_key=str(r["id"]),
            state=r["state"],
            bounds=r["bounds"],
            frame_count=int(r["frame_count"]),
            point_count=int(r["point_count"]),
            nearest_frames=list(r["nearest_frames"]),
        )
        for r in doc["regions"]
    )


@router.get(
    "/inspections/{inspection_id}/coverage",
    response_model=CoverageOut,
    description="Role: viewer. Coverage of the inspection's latest run with coverage: every map "
    "region (voxel in bundle units) with its state inspected_ok | finding | not_seen, bounds, "
    "usable frame count, point count and nearest frames, and the count and percent of regions "
    "per state. status 'not_computed' with a plain message (and no regions) when the coverage "
    "step has not run. 404: unknown inspection.",
)
def get_coverage(inspection_id: int, db: Db, _: Viewer) -> CoverageOut:
    get_or_404(db, Inspection, inspection_id)
    # ponytail: "latest" = the highest run id, as the report's map; by date when runs get one
    run_id = db.scalar(
        select(func.max(CoverageRegion.run_id))
        .join(Run, Run.id == CoverageRegion.run_id)
        .where(Run.inspection_id == inspection_id)
    )
    if run_id is None:
        return CoverageOut(inspection_id=inspection_id, status="not_computed",
                           message=NOT_COMPUTED, run_id=None, totals=None, regions=[])  # fmt: skip
    rows = list(
        db.scalars(
            select(CoverageRegion)
            .where(CoverageRegion.run_id == run_id)
            .order_by(CoverageRegion.id)
        )
    )
    counts = {s: sum(r.state == s for r in rows) for s in STATES}
    totals = {s: StateTotal(count=n, percent=round(100 * n / len(rows), 1))
              for s, n in counts.items()}  # fmt: skip
    regions = [
        RegionOut(id=r.region_key, state=cast(State, r.state), bounds=r.bounds,
                  frame_count=r.frame_count, point_count=r.point_count,
                  nearest_frames=r.nearest_frames)
        for r in rows
    ]  # fmt: skip
    return CoverageOut(inspection_id=inspection_id, status="computed", message=None,
                       run_id=run_id, totals=totals, regions=regions)  # fmt: skip
