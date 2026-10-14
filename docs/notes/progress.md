# Progress

Newest first. Keep it short.

## 2026-10-14 — Part B (pipeline) merged; M0 + M1 complete on public data

**Done**
- `python -m qarau_pipeline run --video …` runs frames → COLMAP → export end to end.
  Re-run by the coordinator on the church video: 21 s, 52/54 frames registered,
  15 215 points, bundle reprojection check mean 0.39 px.
- Three public drone videos and South Building processed; numbers in `docs/notes/m1-results.md`.
- `make lint` and `make test` green for backend, pipeline and web. e2e: 7 Playwright tests
  pass through the real API.
- Deviation: the exposure rule drops on clipped white only above 70 % (was 40 %); bright sky
  made the old rule drop a whole stretch of the Aegviidu orbit.

**Next**
- PR #1 opened (M0 + M1); CI green on GitHub. Waiting for owner review.
- M1 on our own phone footage when the owner has it (pipe, railing, building corner).
- Aegviidu splits into 5 models (127 of 177 frames in the largest): try loop detection or
  merging models.
- Default view frames the whole landscape on drone videos; frame the orbited object instead.

**Open questions for the owner**
- On-prem object store (decide by M5).
- Kazakh UI strings need a native speaker's review.

## 2026-10-14 — Part C (viewer) merged

- `/viewer/:runId` loads the bundle from the real API (via Vite proxy) and from the dev
  middleware. Verified with Playwright on Metal: South Building, the church video and the
  Aegviidu water tower video all load in about 1.5 s and render at 60 fps (vsync cap).
  Synthetic 5 M points: 4.9 s load, 60 fps.
- Screenshots checked by eye: build-up along the video, "seen now" highlight, camera path
  and frustums, view through the video camera matches the real frame, point → frames list.
- Fixed during review: crash on unregistered frames (panel read a field those frames do not
  have), oversized points in close-up views, frustum lines across the through-camera view.
- Known: through-camera view ignores camera roll; XYZ gizmo has no letters; CPU picking and
  no LOD (both marked `ponytail:`); Kazakh strings need a native speaker's review.

## 2026-10-14 — Part A (M0) merged

- `make up` brings up Postgres 16 + PostGIS 3.6 + pgvector 0.8 and Versity S3 Gateway, both
  healthy. `/health` returns 200 with both ok, and 503 within 0.02 s when storage is stopped.
- Runs API serves the South Building bundle; path traversal returns 404. Backend: 10 tests,
  ruff and mypy clean.
- Deviations: Postgres image is `pgvector/pgvector:0.8.0-pg16` + PostGIS from apt, because
  `postgis/postgis` has no arm64 image. Host port 5433 for Postgres. `make api API_PORT=8001`
  on this Mac, because port 8000 is taken by an unrelated container.
- Known: CI not yet run on GitHub; no S3 bucket created yet (nothing writes to S3 in M1).

## 2026-10-14 — resume after usage limit

- First A/B/C subagents stopped at their first step (model usage limit). Their worktrees had
  no changes and were removed. Relaunched A, B, C on Sonnet; this session coordinates and
  reviews before merging into `feat/m1`.
- Owner approved: the M1 plan as written; Versity S3 Gateway for development (ADR 0006).
  On-prem object store stays open until M5.
- `git ls-files data` is empty: no samples or runs are tracked.

## 2026-10-14 — M0 + M1 started

**Done**
- Research and plan: `docs/notes/m1-plan.md`; ADRs 0003–0006.
- Mac set up: FFmpeg + COLMAP (Homebrew), Python 3.12 (uv), pycolmap 4.2.1 wheel verified.
- Public test data in `data/samples/` (gitignored): South Building (COLMAP sample, with
  sparse model), three CC-licensed drone videos (two water towers, one church).
- `.gitignore` blocks video, point clouds, weights, `.env`, `data/`, `scratch/`.
- Initial commit on `main`; work continues on `feat/m1`.

**Next**
- A: infra (Postgres+PostGIS+pgvector, Versity S3), FastAPI `/health` + runs routes, CI, Makefile.
- B: pipeline — export (South Building bundle) → frames → reconstruction → three videos.
- C: viewer — synthetic bundle → South Building → video runs.

**Known issues**
- MinIO images withdrawn upstream → Versity S3 Gateway (ADR 0006).
- No CUDA on Mac → sparse clouds only in M1 (ADR 0004).

**Open questions for the owner**
- On-prem object store (decide by M5).
- No money spent; nothing paid is proposed.
