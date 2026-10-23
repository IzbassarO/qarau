"""Inspection workflow routes (S1): queue a run, read its progress, rerun one step.

POST /api/inspections/{id}/runs                          engineer -> queued Run
GET  /api/inspections/{id}                               viewer   -> inspection + steps
POST /api/inspections/{id}/runs/{run_id}/steps/{step}/rerun  engineer -> queued from that step

The worker (app/workflow.py) writes Run.status / current_step / progress / error and the
run_step rows; this module only reads them and enqueues jobs on RQ (Valkey, ADR 0012).
"""

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, field_validator
from rq import Queue
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import workflow
from .crud import Db, Engineer, InspectionOut, Viewer, audit, get_or_404
from .models import Inspection, Run, RunStep

router = APIRouter(prefix="/api/inspections/{inspection_id}")
ACTIVE = ("queued", "running")


def get_queue() -> Iterator[Queue]:
    yield workflow.queue()


Q = Annotated[Queue, Depends(get_queue)]


class StepOut(BaseModel):
    name: str
    status: str  # pending | running | done | failed
    started_at: datetime | None
    finished_at: datetime | None
    duration_s: float | None
    error: str | None

    @field_validator("started_at", "finished_at")
    @classmethod
    def _utc(cls, v: datetime | None) -> datetime | None:
        """The worker writes UTC; SQLite returns it naive. Always serialize with the zone."""
        return v.replace(tzinfo=UTC) if v is not None and v.tzinfo is None else v


class InspectionStatus(InspectionOut):
    """InspectionOut plus the latest run's progress, in the shape web/src/lib/processing.ts
    parses (status, steps[name, status, started_at, finished_at, duration_s, error])."""

    run_id: str | None = None
    progress: float | None = None
    current_step: str | None = None
    error: str | None = None
    steps: list[StepOut] = []


class RunIn(BaseModel):
    run_id: str | None = None  # default: the inspection's newest run (the uploaded video)
    force: bool = False  # queue even while status says queued/running (after a worker crash)


class Queued(BaseModel):
    run_id: str
    status: str
    job_id: str
    from_step: str | None = None


def latest_run(db: Session, inspection_id: int) -> Run | None:
    stmt = select(Run).where(Run.inspection_id == inspection_id)
    return db.scalars(stmt.order_by(Run.created_at.desc(), Run.id.desc())).first()


def get_run(db: Session, inspection_id: int, run_id: str) -> Run:
    run = db.get(Run, run_id)
    if run is None or run.inspection_id != inspection_id:
        raise HTTPException(404, "run not found")
    return run


def steps_of(db: Session, run_id: str) -> list[StepOut]:
    rows = db.scalars(select(RunStep).where(RunStep.run_id == run_id).order_by(RunStep.id))
    return [StepOut.model_validate(r, from_attributes=True) for r in rows]


@router.get(
    "",
    description="Role: viewer. One inspection with its latest run's status, progress, "
    "current step and the step list (name, status, started_at, finished_at, duration_s, error).",
)
def get_inspection(inspection_id: int, db: Db, _: Viewer) -> InspectionStatus:
    insp = get_or_404(db, Inspection, inspection_id)
    out = InspectionStatus.model_validate(insp)
    run = latest_run(db, inspection_id)
    if run is not None:
        out.run_id, out.progress = run.id, run.progress
        out.current_step, out.error = run.current_step, run.error
        out.steps = steps_of(db, run.id)
    return out


@router.post(
    "/runs",
    status_code=202,
    description="Role: engineer. Queue the workflow (probe -> frames -> sparse -> export) for the "
    "inspection's newest run, or for `run_id`. The run needs its uploaded video. 409 while the "
    "run is queued or running unless `force` (the escape after a crashed worker); finished steps "
    "are skipped by the pipeline, so re-queueing a failed or interrupted run redoes only the rest.",
)
def queue_run(
    inspection_id: int, body: RunIn, db: Db, who: Engineer, request: Request, q: Q
) -> Queued:
    get_or_404(db, Inspection, inspection_id)
    run = get_run(db, inspection_id, body.run_id) if body.run_id else latest_run(db, inspection_id)
    if run is None:
        raise HTTPException(409, "no run to process: upload a video first")
    if run.status == "uploading":
        raise HTTPException(409, "the video upload is not complete")
    if run.status in ACTIVE and not body.force:
        raise HTTPException(409, f"run is already {run.status}; pass force to queue it again")
    return _queue(db, who, request, q, run, None)


@router.post(
    "/runs/{run_id}/steps/{step}/rerun",
    status_code=202,
    description="Role: engineer. Rerun one step and the ones after it (forced, even if done); "
    "earlier finished steps are not redone. 409 while the run is queued or running.",
)
def rerun_step(
    inspection_id: int, run_id: str, step: str, db: Db, who: Engineer, request: Request, q: Q
) -> Queued:
    run = get_run(db, inspection_id, run_id)
    if run.status in ACTIVE:
        raise HTTPException(409, f"run is {run.status}; wait for it to finish")
    if run.status == "uploading":
        raise HTTPException(409, "the video upload is not complete")
    known = {s.name for s in steps_of(db, run.id)}
    if known and step not in known:  # before the first run the worker checks the name itself
        raise HTTPException(404, f"unknown step '{step}'; steps: {', '.join(sorted(known))}")
    return _queue(db, who, request, q, run, step)


def _queue(
    db: Session, who: Engineer, request: Request, q: Queue, run: Run, from_step: str | None
) -> Queued:
    run.status, run.error = "queued", None
    if from_step is None:
        run.current_step = None
    insp = db.get(Inspection, run.inspection_id)
    if insp is not None:
        insp.status = "queued"
    audit(db, who, request, "run", run.id, "queue" if from_step is None else f"rerun:{from_step}")
    db.commit()  # the row says queued before the worker can pick the job up
    job_id = workflow.enqueue(q, run.id, from_step)
    return Queued(run_id=run.id, status="queued", job_id=job_id, from_step=from_step)
