"""Inspection workflow routes (S1): queue a run, read its progress, rerun one step.

POST /api/inspections/{id}/runs                          engineer -> queued Run
GET  /api/inspections/{id}                               viewer   -> inspection + steps
POST /api/inspections/{id}/runs/{run_id}/steps/{step}/rerun  engineer -> queued from that step

The worker (app/workflow.py) writes Run.status / current_step / progress / error and the
`steps` list; this module only reads them and enqueues jobs on RQ (Valkey, ADR 0012).
"""

from collections.abc import Iterator
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from rq import Queue
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import workflow
from .auth import check_inspection
from .crud import Db, Engineer, InspectionOut, ViewerOrGuest, audit, get_or_404
from .models import Inspection, Run

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


class InspectionStatus(InspectionOut):
    """InspectionOut plus the latest run's progress, in the shape web/src/lib/processing.ts
    parses (status, steps[name, status, started_at, finished_at, duration_s, error])."""

    run_id: str | None = None
    progress: int | None = None
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


def steps_of(run: Run) -> list[StepOut]:
    return [StepOut.model_validate(s) for s in run.steps]


@router.get(
    "",
    description="Role: viewer. One inspection with its latest run's status, progress, "
    "current step and the step list (name, status, started_at, finished_at, duration_s, error). "
    "A guest: 404 unless every run of the inspection is shared with its event.",
)
def get_inspection(inspection_id: int, db: Db, who: ViewerOrGuest) -> InspectionStatus:
    insp = get_or_404(db, Inspection, inspection_id)
    check_inspection(db, who, inspection_id)
    out = InspectionStatus.model_validate(insp)
    run = latest_run(db, inspection_id)
    if run is not None:
        out.run_id, out.progress = run.id, run.progress
        out.current_step, out.error = run.current_step, run.error
        out.steps = steps_of(run)
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
    known = {s.name for s in steps_of(run)}
    if known and step not in known:  # before the first run the worker checks the name itself
        raise HTTPException(404, f"unknown step '{step}'; steps: {', '.join(sorted(known))}")
    if run.status in ACTIVE:
        raise HTTPException(409, f"run is {run.status}; wait for it to finish")
    if run.status == "uploading":
        raise HTTPException(409, "the video upload is not complete")
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
    job_id = run.rq_job_id = workflow.enqueue(q, run.id, from_step)
    db.commit()  # only rq_job_id: a worker that already started keeps its status fields
    return Queued(run_id=run.id, status="queued", job_id=job_id, from_step=from_step)
