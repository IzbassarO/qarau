"""The RQ job that runs an inspection's workflow steps and mirrors their state to Postgres.

`run_inspection(run_id, from_step)` runs the pipeline steps in order (S1, the pipeline's
`product_steps`: probe -> frames -> sparse -> export -> coverage [-> dense]), one subprocess
per step (`python -m qarau_pipeline step <name> --run <dir> [--force]`, ADR 0022), so the backend
environment never imports pycolmap. After every transition it writes Run.status
(queued | running | done | failed), current_step, progress (finished steps / total, 0-100), error
and the `steps` list (one {name, status, started_at, finished_at, duration_s, error} per step),
and mirrors the status on the Inspection. The pipeline's own <run>/<step>/step.json markers
make the job idempotent: re-enqueueing a run after a worker crash redoes only the steps without
a `done` marker. `from_step` forces that step and the ones after it; earlier steps are not
touched. After the `coverage` step, `fill_coverage` replaces the run's CoverageRegion rows with
the regions of <run>/coverage/regions.json (COV1-b).

The step runner is a protocol (`StepRunner`) so tests plug in in-process fakes via `make_runner`.
"""

from __future__ import annotations

import json
import logging
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from redis import Redis
from rq import Queue
from sqlalchemy import delete
from sqlalchemy.orm import Session

from .db import engine_for
from .models import CoverageRegion, CoverageState, Inspection, Run
from .settings import get_settings
from .storage import prefix_of

log = logging.getLogger(__name__)
QUEUE = "qarau"
JOB_TIMEOUT_S = 6 * 3600  # COLMAP on a long video; the pipeline's own benchmark cap is 30 min/video
StepDoc = dict[str, Any]  # the content of <run>/<step>/step.json


class StepRunner(Protocol):
    def names(self) -> list[str]:
        """Step names in order (`python -m qarau_pipeline step --list`)."""
        ...

    def run(self, run_dir: Path, name: str, force: bool, prefix: str) -> StepDoc:
        """Run one step; return its step.json (status done | failed, timestamps, error).
        `prefix` is the run's storage prefix <org>/<site>/<inspection> (ADR 0024)."""
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

    def run(self, run_dir: Path, name: str, force: bool, prefix: str) -> StepDoc:
        cmd = self._cmd(name, "--run", str(run_dir), "--prefix", prefix,
                        *(["--force"] if force else []))  # fmt: skip
        run_dir.mkdir(parents=True, exist_ok=True)  # a wiped cache dir: the step refills it
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
    return Queue(QUEUE, connection=connection or Redis.from_url(get_settings().valkey_url))


def enqueue(q: Queue, run_id: str, from_step: str | None = None) -> str:
    job = q.enqueue(run_inspection, run_id, from_step, job_timeout=JOB_TIMEOUT_S, result_ttl=0)
    return str(job.id)


# ---------------------------------------------------------------- the job


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


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


def _pending(name: str) -> StepDoc:
    return {"name": name, "status": "pending", "started_at": None, "finished_at": None,
            "duration_s": None, "error": None}  # fmt: skip


def _set_step(run_id: str, name: str, **fields: Any) -> None:
    """Update one entry of Run.steps (the list is replaced, so SQLAlchemy sees the change)."""

    def go(db: Session) -> None:
        run = db.get(Run, run_id)
        if run is None:
            raise RuntimeError(f"run {run_id} vanished")
        steps = [dict(s) for s in run.steps]
        row = next((s for s in steps if s["name"] == name), None)
        if row is None:
            row = _pending(name)
            steps.append(row)
        row.update(fields)
        run.steps = steps

    _write(go)


def run_inspection(run_id: str, from_step: str | None = None) -> str:
    """RQ job. Returns the final Run.status ('done' | 'failed')."""
    runner = make_runner()
    with Session(engine_for(get_settings().database_url)) as db:
        run = db.get(Run, run_id)
        if run is None:
            raise RuntimeError(f"run {run_id} not found")
        run_dir = Path(get_settings().qarau_runs_dir).resolve() / run.run_dir
        prefix = prefix_of(db, run)
        old = {s["name"]: dict(s) for s in run.steps}
    names = runner.names()
    if from_step is not None and from_step not in names:
        _set_run(run_id, status="failed", error=f"unknown step '{from_step}'")
        return "failed"
    start = names.index(from_step) if from_step else 0
    force = from_step is not None
    # Every step has an entry; the ones to run (re)start as pending, earlier ones keep theirs.
    steps = [old.get(n, _pending(n)) if i < start else _pending(n) for i, n in enumerate(names)]
    _set_run(run_id, status="running", current_step=names[start], error=None,
             progress=100 * start // len(names), steps=steps)  # fmt: skip
    for i, name in enumerate(names[start:], start):
        _set_step(run_id, name, status="running", started_at=_now())
        _set_run(run_id, current_step=name)
        try:
            doc = runner.run(run_dir, name, force, prefix)
        except Exception as e:  # noqa: BLE001 - a runner crash is this step's failure
            log.exception("step %s of run %s crashed", name, run_id)
            doc = {"status": "failed", "error": f"{type(e).__name__}: {e}"}
        ok = doc.get("status") == "done"
        _set_step(
            run_id, name,
            status="done" if ok else "failed",
            started_at=doc.get("started_at") or _now(),
            finished_at=doc.get("finished_at") or _now(),
            duration_s=doc.get("duration_s"),
            error=None if ok else str(doc.get("error") or "failed without a message"),
        )  # fmt: skip
        if not ok:
            _set_run(run_id, status="failed", error=f"{name}: {doc.get('error')}")
            return "failed"
        if name == "coverage":
            fill_coverage(run_id, run_dir)
        _set_run(run_id, progress=100 * (i + 1) // len(names))
    _set_run(run_id, status="done", current_step=None, progress=100)
    return "done"


def fill_coverage(run_id: str, run_dir: Path) -> int:
    """Replace the run's CoverageRegion rows with <run_dir>/coverage/regions.json in one
    transaction (a rerun never duplicates rows); returns the row count. `coverage/` is not a
    storage kind: the step has just written the file into the run's cache dir. A missing or
    unreadable file leaves no rows and a warning; it never fails the run."""
    path = run_dir / "coverage" / "regions.json"
    try:
        rows = [
            CoverageRegion(
                run_id=run_id, region_key=str(r["id"]), state=CoverageState(r["state"]),
                frame_count=int(r["frame_count"]),
                bounds_min=[float(x) for x in r["bounds"]["min"]],
                bounds_max=[float(x) for x in r["bounds"]["max"]],
            )
            for r in json.loads(path.read_text())["regions"]
        ]  # fmt: skip
    except (OSError, ValueError, KeyError, TypeError) as e:
        log.warning("run %s: no coverage regions stored, %s is missing or unreadable: %s",
                    run_id, path, e)  # fmt: skip
        rows = []

    def go(db: Session) -> None:
        db.execute(delete(CoverageRegion).where(CoverageRegion.run_id == run_id))
        db.add_all(rows)

    _write(go)
    return len(rows)
