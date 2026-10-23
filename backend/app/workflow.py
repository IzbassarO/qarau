"""The RQ job that runs an inspection's workflow steps and mirrors their state to Postgres.

`run_inspection(run_id, from_step)` runs the pipeline steps in order, one subprocess per step
(`python -m qarau_pipeline step <name> --run <dir> [--force]`, ADR 0022), so the backend
environment never imports pycolmap. After every transition it writes Run.status
(queued | running | done | failed), current_step, progress (finished steps / total, %), error
and one RunStep row per step, and mirrors the status on the Inspection. The pipeline's own
<run>/<step>/step.json markers make the job idempotent: re-enqueueing a run after a worker
crash redoes only the steps without a `done` marker. `from_step` forces that step and the ones
after it; earlier steps are not touched.

The step runner is a protocol (`StepRunner`) so tests plug in in-process fakes via `make_runner`.
"""

from __future__ import annotations

import json
import logging
import subprocess
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from redis import Redis
from rq import Queue
from sqlalchemy.orm import Session

from .db import engine_for
from .models import Inspection, Run, RunStep, now
from .settings import get_settings

log = logging.getLogger(__name__)
QUEUE = "qarau"
JOB_TIMEOUT_S = 6 * 3600  # COLMAP on a long video; the pipeline's own benchmark cap is 30 min/video
StepDoc = dict[str, Any]  # the content of <run>/<step>/step.json


class StepRunner(Protocol):
    def names(self) -> list[str]:
        """Step names in order (`python -m qarau_pipeline step --list`)."""
        ...

    def run(self, run_dir: Path, name: str, force: bool) -> StepDoc:
        """Run one step; return its step.json (status done | failed, timestamps, error)."""
        ...


class SubprocessRunner:
    """One `qarau_pipeline step` child per step; stdout+stderr appended to <run>/log.txt."""

    def __init__(self, pipeline_dir: Path) -> None:
        self.pipeline_dir = pipeline_dir
        self.python = pipeline_dir / ".venv" / "bin" / "python"

    def _cmd(self, *args: str) -> list[str]:
        return [str(self.python), "-m", "qarau_pipeline", "step", *args]

    def names(self) -> list[str]:
        out = subprocess.run(  # noqa: S603 - our own interpreter and fixed arguments
            self._cmd("--list"), cwd=self.pipeline_dir, capture_output=True, text=True, check=True
        ).stdout
        names: list[str] = json.loads(out)
        return names

    def run(self, run_dir: Path, name: str, force: bool) -> StepDoc:
        cmd = self._cmd(name, "--run", str(run_dir), *(["--force"] if force else []))
        with (run_dir / "log.txt").open("a") as f:
            f.write(f"\n$ {' '.join(cmd)}\n")
            f.flush()
            p = subprocess.run(  # noqa: S603 - our own interpreter; name comes from names()
                cmd, cwd=self.pipeline_dir, stdout=f, stderr=subprocess.STDOUT
            )
        marker = run_dir / name / "step.json"
        if marker.is_file():
            doc: StepDoc = json.loads(marker.read_text())
            return doc
        lines = (run_dir / "log.txt").read_text().strip().splitlines()
        tail = lines[-1] if lines else "no output"
        return {"status": "failed", "error": f"step process exit {p.returncode}: {tail}"}


def make_runner() -> StepRunner:
    """Replaced in tests with in-process fakes."""
    return SubprocessRunner(Path(get_settings().qarau_pipeline_dir).resolve())


def queue(connection: Redis | None = None) -> Queue:
    return Queue(QUEUE, connection=connection or Redis.from_url(get_settings().queue_url))


def enqueue(q: Queue, run_id: str, from_step: str | None = None) -> str:
    job = q.enqueue(run_inspection, run_id, from_step, job_timeout=JOB_TIMEOUT_S, result_ttl=0)
    return str(job.id)


# ---------------------------------------------------------------- the job


def _parse(ts: str | None) -> datetime | None:
    return datetime.fromisoformat(ts) if ts else None


def _write(fn: Callable[[Session], None]) -> None:
    """One short transaction per state change: a long step never holds a database lock."""
    with Session(engine_for(get_settings().database_url)) as db:
        fn(db)
        db.commit()


def _set_run(run_id: str, **fields: Any) -> None:
    def go(db: Session) -> None:
        run = db.get(Run, run_id)
        if run is None:
            raise RuntimeError(f"run {run_id} vanished")
        for k, v in fields.items():
            setattr(run, k, v)
        insp = db.get(Inspection, run.inspection_id)
        if insp is not None and "status" in fields:
            insp.status = fields["status"]

    _write(go)


def _set_step(run_id: str, name: str, **fields: Any) -> None:
    def go(db: Session) -> None:
        row = db.query(RunStep).filter_by(run_id=run_id, name=name).one_or_none()
        if row is None:
            row = RunStep(run_id=run_id, name=name)
            db.add(row)
        for k, v in fields.items():
            setattr(row, k, v)

    _write(go)


def run_inspection(run_id: str, from_step: str | None = None) -> str:
    """RQ job. Returns the final Run.status ('done' | 'failed')."""
    runner = make_runner()
    with Session(engine_for(get_settings().database_url)) as db:
        run = db.get(Run, run_id)
        if run is None:
            raise RuntimeError(f"run {run_id} not found")
        run_dir = Path(get_settings().qarau_runs_dir).resolve() / run.run_dir
    names = runner.names()
    if from_step is not None and from_step not in names:
        _set_run(run_id, status="failed", error=f"unknown step '{from_step}'")
        return "failed"
    start = names.index(from_step) if from_step else 0
    force = from_step is not None
    for name in names:  # every step has a row; the ones to run (re)start as pending
        if name in names[start:]:
            _set_step(run_id, name, status="pending", started_at=None, finished_at=None,
                      duration_s=None, error=None)  # fmt: skip
        else:
            _set_step(run_id, name)
    _set_run(run_id, status="running", current_step=names[start], error=None,
             progress=round(100 * start / len(names), 1))  # fmt: skip
    for i, name in enumerate(names[start:], start):
        _set_step(run_id, name, status="running", started_at=now())
        _set_run(run_id, current_step=name)
        try:
            doc = runner.run(run_dir, name, force)
        except Exception as e:  # noqa: BLE001 - a runner crash is this step's failure
            log.exception("step %s of run %s crashed", name, run_id)
            doc = {"status": "failed", "error": f"{type(e).__name__}: {e}"}
        ok = doc.get("status") == "done"
        _set_step(
            run_id, name,
            status="done" if ok else "failed",
            started_at=_parse(doc.get("started_at")) or now(),
            finished_at=_parse(doc.get("finished_at")) or now(),
            duration_s=doc.get("duration_s"),
            error=None if ok else str(doc.get("error") or "failed without a message"),
        )  # fmt: skip
        if not ok:
            _set_run(run_id, status="failed", error=f"{name}: {doc.get('error')}")
            return "failed"
        _set_run(run_id, progress=round(100 * (i + 1) / len(names), 1))
    _set_run(run_id, status="done", current_step=None, progress=100.0)
    return "done"
