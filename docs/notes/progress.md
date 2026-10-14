# Progress

Newest first. Keep it short.

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
- Confirm Versity S3 Gateway (Apache-2.0) as the on-prem object store, or name a preference.
- No money spent; nothing paid is proposed.
