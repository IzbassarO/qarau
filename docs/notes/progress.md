# Progress

Newest first. Keep it short.

## 2026-10-14 — Dev team (`devteam/`) v0 → v1; no human review

- Owner's decision: no human review. Work merges into `dev` only through automated gates
  (local checks, LLM tester, GitHub CI, benchmark, gitleaks); weekly `dev` → `main` release.
  Rules in CLAUDE.md → "Delivery"; design in `docs/notes/devteam-plan.md`.
- PR #2 (M2 prep + room test) merged into `main`; `dev` recreated from `main` and pushed.
- devteam: LangGraph graph (brain → per-task subgraph worktree → develop → test → gates),
  SQLite checkpoints + `resume`, `stop`, atomic $3/day API budget, cost report, daily report.
  Two Codex read-only reviews; all findings fixed with tests (40 tests).
- v1: brain = Opus 5.5 (API), dev_pipeline = Claude Code headless (subscription),
  tester = Sonnet 5.5 (API). First real task P1 merged automatically (PR #3) after 3 rounds; API cost $0.31.
- Phase 1 plan: `docs/plan/phase1.md`; backlog `devteam/backlog.yaml` (P1–P7, D1, A1–A4).

**Waiting for the owner**
- GitHub branch protection on `main` needs GitHub Pro (~$4/month) for a private repo;
  `devteam/hooks/pre-push` stands in locally until then.
- PostGIS is GPL-2.0: fine as a server, but on-prem installs that ship it must follow the GPL.
## 2026-10-14 — P1: benchmark command (`devteam/P1`)

- `python -m qarau_pipeline benchmark --data D --out O [--only …] [--timeout-s N] [--record]`:
  manifest `pipeline/qarau_pipeline/benchmark_set.toml` (4 public videos required, room
  optional), one child `run` per video with timeout, `O/results.json` + `results.md`, exit 0
  only if all ran. `--record` → `docs/benchmarks/<sha>.json` + rows in `benchmark.md`.
  Method: `docs/notes/benchmark-method.md`. Tests mock the child run (< 1 s).
- Product defaults (300 frames, exhaustive matching) on Aegviidu and Orono took > 28 min of
  matching each on the M1 Pro and timed out at 1800 s. The benchmark caps those two at
  150 frames (`max_frames` in the manifest, same code path; reasons in
  `benchmark-method.md`) so the set fits the gate's 90 min. Recorded run (a8efcc9+dirty,
  all 5 exit 0, required set 62 min on a loaded M1 Pro): aegviidu 132/150, 3 models,
  0.53 px; mykolaiv 178/210, 3 models, 0.74 px; notre-dame 109/109, 0.40 px; orono 150/150,
  0.57 px, 92 k points; room 257/269, 0.93 px.
- **Gate risk:** mykolaiv points/reproj swing +23 %/+16 % between identical runs, which is
  beyond the gate thresholds (`benchmark-method.md`, Variance).
- **Known issue (for the tech lead):** a real 300-frame drone video takes ~45 min on CPU. That
  is a product problem (sequential + loop matching for video? fewer features?). Measure the fix
  with this benchmark in its own task.
- Next: `scale_err_cm` once a scale reference exists (P5/P7); image-folder datasets in the set.

## 2026-10-14 — New direction: MVP plan; Step 1 room test (`feat/room-test`)

- Owner's brief arrived **without the plan text** (placeholder `<paste the plan here>`):
  `docs/plan/mvp.md`, roadmap update and `docs/tasks/phase1.md` wait for it.
- Room video (`IMG_8315.MOV`, iPhone HDR portrait): M1 registered 14 of 111 frames. Fixed in
  the frames/reconstruction stages (HDR→SDR, long-side cap, 6 fps, exhaustive matching ≤ 300)
  → 244 of 269, 26 k points. Details: `docs/notes/room-test.md`, `docs/notes/benchmark.md`.
- No A4 visible in the video → scale check needs a re-shoot.
- "Original colours" toggle already existed in the viewer; checked on the room.
- R and D agent worktrees from M2 prep left as they are (D has the dense stage, unmerged).

## 2026-10-14 — M2 prep resumed after the session stopped

- Merged: V (roll-exact camera view, object-focused initial view, bundle v2 reader,
  findings UI) and T (datasets, `docs/notes/datasets.md`, Raketa shipwreck drone video).
- R, D, S agents had stopped mid-way; relaunched in their own worktrees from where they were:
  - R: PostGIS test committed; recon eval done on notre-dame (global mapper + loop +
    overlap 20 registers 54/54 vs 52/54), Aegviidu baseline only (6 models, 127/177).
  - D: Depth Anything V2 Small (Apache-2.0) scaled to sparse COLMAP points → 7.1 M points on
    Orono (held-out depth error median 4.3 %); export to bundle v2 and viewer check pending.
  - S: segmenters + projection committed; real-footage run (Raketa) and ADR pending.

## 2026-10-14 — M2 prep started (`feat/m2-prep`)

**Done**
- PR #1 (M0 + M1) merged into `main` after owner review; CI green.
- Task `docs/tasks/M2.md` and plan `docs/notes/m2-plan.md` (bundle v2 + findings contract,
  `Segmenter`/`Densifier` interfaces, five parallel parts R/D/S/V/T). `ml` dependency group
  (torch, transformers) kept out of default `uv sync`.

**In progress (Sonnet agents, coordinator reviews before merge)**
- R: reconstruction eval (loop detection / overlap / global mapper) on all four runs, ADR 0007; PostGIS test + CI.
- D: densification without CUDA, commercial-licence models only, ≥ 1 M points on Orono, ADR 0008.
- S: segmenters (manual, local dev-only, SAM 3.1 API stub), mask → 3D projection, voting, findings, ADR 0009.
- V: through-camera roll, initial view on the object, bundle v2 reader, findings UI.
- T: CC-licensed defect footage + segmentation dataset, `docs/notes/datasets.md`.

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
