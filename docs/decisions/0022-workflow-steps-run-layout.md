# 0022 — Workflow steps: per-step run layout with done markers, subprocess-per-step RQ runner

- Status: accepted (dev team, S1)
- Date: 2026-10-23

## Context

Until S1 the pipeline was a hand-run CLI (`run`) writing one flat run dir (`frames/`, `frames.json`,
`quality_report.json`, `colmap/`, `web/`, `cloud.*`), and the backend never learned whether a run
was going. S1 puts the run on a queue (RQ on Valkey, ADR 0012) and needs: idempotent steps (a
worker killed mid-step must redo only that step), per-step progress in Postgres for the web app,
a rerun of one failed step, and a step interface that a later orchestrator (Temporal) can drive
without changing the steps.

## Decision

1. **Step = `step(ctx) -> StepResult`, outputs under `<run>/<step>/`, done marker
   `<run>/<step>/step.json`** (`pipeline/qarau_pipeline/workflow.py`). The marker is written
   atomically after the body returns (`done` or `failed`); the whole step dir is cleared before a
   rerun, so no marker = not done and a half-written output never survives into the redo. A step
   whose marker says `done` is skipped unless forced. Steps read inputs from
   the run dir and know nothing about queues or databases. The product steps are `probe`,
   `frames`, `sparse`, `export` and, only when `config.yaml` → `workflow.dense` is true, `dense`
   (fake provider only; Modal is refused there, ADR 0019). The steps still call `run_stage` with
   the old names (`frames`, `reconstruction`, `export`), so `run.json`, the manifest's
   `processing_s` and the benchmark numbers are unchanged.
2. **Run layout moves under the step dirs** (`layout.py`): `frames/frames.json`,
   `frames/quality_report.json`, `sparse/` (was `colmap/`), `export/{web, cloud.laz, cloud.ply}`
   (was at the root), plus `probe/`, `dense/`. Manifest paths stay relative to the run root
   (`export/web/…`), so the API's file route and the web app need no change. **Legacy fallback:**
   every helper returns the old path when a run dir already has it and not the new one, so the
   existing runs under `data/runs` (and Kerege's refresh runs) stay readable, listable and
   re-exportable in place; only new runs get the new layout. `run.json`, `log.txt`,
   `workflow.json` and the uploaded `video.<ext>` stay at the run root: they belong to the run,
   not to a step.
3. **Runner: RQ job, one subprocess per step** (`backend/app/workflow.py`). The job runs
   `python -m qarau_pipeline step <name> --run <dir> [--force]` with the pipeline's own venv, so
   the API/worker environment never imports pycolmap/OpenCV, a COLMAP crash cannot take the
   worker down, and the step's stdout lands in `<run>/log.txt`. After every transition the job
   writes `Run.status / current_step / progress (integer 0-100) / error / rq_job_id` and
   `Run.steps` (migration v0002) in one short transaction each; the Inspection mirrors the run
   status. `Run.steps` is a **JSON list** on the run, not a table: the list is the step order,
   every transition is one UPDATE of one row, the API returns it without a join, and the fixed
   four to five entries are never queried on their own. The runner is a protocol (`StepRunner`)
   so tests use in-process fakes with fakeredis + `SimpleWorker`.
4. **Queueing a run reuses B1's Run.** `POST /api/inspections/{id}/runs` queues the inspection's
   newest run (the one the upload created, which holds the video) instead of creating an empty
   one; `force` queues a run whose status is stuck in `running` after a worker crash. The rerun
   route queues `from_step` with force for that step and the ones after it.

## Consequences

- A rerun of `run --out <existing>` now skips finished steps; `--force` redoes them.
- The dense step on the workflow is fake-only by design; the real dense path stays the manual
  `dense-remote --provider modal --allow-paid-gpu` (ADR 0019).
- A worker killed with SIGKILL leaves `Run.status = running`; the engineer re-queues with
  `force` (or reruns a step). Automatic detection of dead jobs is not built (RQ marks them only
  after the job timeout); add a heartbeat when it bites. In Docker, stopping the worker
  container also kills the running step child; on a bare host, killing only the `rq` process
  leaves the `qarau_pipeline step` child running to completion (it then writes its own marker,
  which the re-queued run simply finds and skips).
- The queue address is one setting, `VALKEY_URL` (`redis://` scheme: the redis-py client talks
  to Valkey), read by the API and the worker alike.
- The worker image (`infra/worker/Dockerfile`) is `linux/amd64` only: pycolmap 4.2.1 has no
  linux/arm64 wheel. On Apple silicon it runs under emulation (slow COLMAP, fine for the queue
  plumbing); the sparse step of real videos is best run natively (`uv run python -m
  qarau_pipeline run`) until the GPU server exists.
- Temporal (or any other orchestrator) replaces only `backend/app/workflow.py`.
