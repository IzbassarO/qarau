# S1 — Inspection workflow: idempotent steps on a queue: plan

Date: 2026-10-23. Scope: `pipeline/`, `backend/`, `infra/`, `docs/`. No S3 (D1), no web UI (U2), no
Modal, no Temporal.

## Steps

1. **Step contract** (`pipeline/qarau_pipeline/workflow.py`): `StepContext(run_dir, config, force,
   log)`, `StepResult(status ok|skipped|failed, outputs, metrics, error)`, `Step(name, fn)`.
   `run_step` skips a step whose `<run>/<step>/step.json` says `done` (unless `force`), removes a
   stale marker before running, and writes the marker atomically (temp file + `os.replace`) with
   `name, status, started_at, finished_at, duration_s, error, outputs, metrics`. A crash leaves no
   marker. `run_steps(run_dir, steps, from_step, force, on_event)` runs a slice in order and calls
   `on_event(name, "start"|"end", result)`; it knows nothing about queues.
   Product steps `probe → frames (+ quality) → sparse → export [→ dense]`; `dense` is listed only
   when `pipeline/config.yaml` → `workflow.dense` is true (default false) and uses the fake
   provider only; the Modal provider is refused (ADR 0019: paid runs are started by hand).
   Each step body still calls `run_stage(...)` with the legacy names (`frames`, `reconstruction`,
   `export`), so `run.json`, the manifest's `processing_s` and the benchmark numbers do not change.
2. **Run layout** (`pipeline/qarau_pipeline/layout.py`): outputs move under `<run>/<step>/`:
   `probe/probe.json`; `frames/{000000.jpg…, manifest.json, frames.json, quality_report.json}`;
   `sparse/{database.db, masks/, sparse/0}`; `export/{web/, cloud.laz, cloud.ply}`; `dense/`.
   Helpers (`frames_json`, `quality_report`, `sparse_dir`, `export_dir`, `manifest_path`) fall back
   to the legacy paths (`frames.json`, `quality_report.json`, `colmap/`, `web/` at the run root)
   when a run dir already has them, so every existing run keeps working. Manifest paths stay
   relative to the run root (`export/web/points.position.f32`), which the web app follows as is.
   `cli.py run` becomes a thin wrapper over `run_steps`; `export.py`, `frames.py`, `refresh.py`,
   `segmentation.py`, `projection.py` use the helpers. New CLI: `step <name> --run DIR [--force]`
   and `step --list`; `run` gets `--force`. `run` writes `<run>/workflow.json` (the input and the
   COLMAP parameters) so a later `step` rerun uses the same parameters.
3. **Backend runner** (`backend/app/workflow.py`): RQ job `run_inspection(run_id, from_step)`;
   a `StepRunner` protocol with the subprocess implementation (`<pipeline>/.venv/bin/python -m
   qarau_pipeline step …`, output appended to `<run>/log.txt`) and in-process fakes in tests. After
   every transition it writes `Run.status (queued|running|done|failed)`, `current_step`, `progress`
   (finished steps / total × 100), `error`, mirrors the status on `Inspection.status`, and keeps one
   `run_step` row per step. Migration `v0002`: `run.current_step`, `run.error`, `run.created_at`,
   table `run_step`. Re-enqueueing a run after a worker crash reruns only the steps without a
   `done` marker (pipeline idempotence; the skipped steps keep their original timestamps).
4. **API** (`backend/app/inspections.py`): `POST /api/inspections/{id}/runs` (engineer) queues the
   inspection's uploaded Run (B1 creates the Run with the video; body `{run_id?, force?}`; 409 while
   queued/running unless `force`, which is the escape after a crashed worker); `GET
   /api/inspections/{id}` (viewer) adds `run_id, progress, current_step, error, steps[{name, status,
   started_at, finished_at, duration_s, error}]`; `POST …/runs/{run_id}/steps/{step}/rerun`
   (engineer) queues `from_step=step` with force for that step and the ones after it.
5. **Infra**: `infra/docker-compose.yml` gets `valkey` (`valkey/valkey:8`, BSD-3) and `worker`
   (`infra/worker/Dockerfile`: python 3.12 + ffmpeg + both uv projects, `linux/amd64` because
   pycolmap ships no linux/arm64 wheel; runs `rq worker` against `QUEUE_URL`, `data/runs` mounted).
   `backend/app/settings.py`: `queue_url`, `qarau_pipeline_dir`.
6. **Tests**: `pipeline/tests/test_workflow.py` (fake steps count calls; crash leaves no marker;
   `from_step`; layout fallback), `backend/tests/test_workflow_api.py` (fakeredis + `SimpleWorker`
   in a thread: status sequences, rerun slice, blocked step vs `/health`, 403 for viewer),
   `test_migrations.py` (v0002), `test_openapi.py`, `test_roles.py`.
7. **Verify**: real video through the B1 upload → queue → steps; kill the worker during `sparse`,
   restart, only `sparse` + `export` rerun; quick benchmark `notre-dame,room` before/after; ruff,
   mypy, pytest in both projects; compose `valkey` + `worker` start.

## Decisions recorded

ADR 0022: per-step run-dir layout with legacy fallback; subprocess-per-step runner on RQ.
