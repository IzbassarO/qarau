# Progress

Newest first. Keep it short.

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
