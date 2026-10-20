# Refresh viewer runs (P3e)

The web app listed runs made before the parallax keyframes (P2b) and the sky mask + far layer
(P3b). `refresh` re-runs every reference video through the current pipeline and exports each
as a NEW run next to the old ones. Reference: `docs/architecture.md`, "Benchmark".

## Command

```sh
cd pipeline
uv run python -m qarau_pipeline refresh --data ../data/samples --runs ../data/runs \
  --suffix p3e [--only notre-dame,room] [--work DIR] [--report ../docs/notes/p3e-refresh-report.md]
```

- Videos: those in `pipeline/qarau_pipeline/benchmark_set.toml` (aegviidu, mykolaiv,
  notre-dame, orono, room) with the same per-video settings (`max_frames`). `--only` limits
  the set and runs it in the given order.
- Naming rule: each video becomes `<runs>/<benchmark name>-<suffix>`, e.g. `notre-dame-p3e`.
  Pick a suffix no run uses yet (a task id or a date).
- `created_at` in `<run>/web/manifest.json` is the export time in UTC (ISO-8601), written by
  `export.py`; `backend/app/runs.py` lists it, so the web app sorts the new runs first.
- Report: a table on stdout (run, registered %, points, far-layer points, keyframes, time,
  status) and, with `--report`, the same table as Markdown in that file (refused inside
  `--runs`). `--timeout-s` is per video (default 1800). Exit 1 if a video failed; a failed
  video never stops the others.
- Without `--only`, other `data/samples` entries (image sets, archives, other clips) are listed
  as `skipped: not a reference video`.

### CPU, sparse only

Per video, a child `qarau_pipeline run --video` (the benchmark's `run_video`): probe, frames
(HDR→SDR), parallax keyframes, COLMAP sparse with the sky mask, `point_filter`, export with the
far layer. No dense stage, no `dense_remote`, no GPU, nothing paid; tests assert the child
command and that the dense entry points are never called. Licences: COLMAP/pycolmap
(BSD-3-Clause), OpenCV (Apache-2.0), NumPy (BSD-3-Clause), Open3D (MIT), FFmpeg as an external
binary; no new library or model, VGGT not used.

### No-overwrite guarantee

- A video whose `<video>-<suffix>` already exists in `--runs` (directory, file or symlink, even
  dangling) fails with `failed: run already exists` before any work; nothing under that path
  is opened.
- Each run is built under its final name in a temp dir OUTSIDE `--runs` (`--work`, default
  the system temp dir; it must be on the same filesystem) and moved in with one `rename` only
  when complete, after re-checking that the name is still free. A failed or timed-out video
  leaves nothing in `--runs`; its temp dir is removed, the reason is reported and the next
  video runs.
- The command writes nothing else into `--runs`: no summary, no manifest, no temp dir. It
  never deletes, moves or rewrites an existing entry there.

### Runs are data

Runs live in `data/runs` (gitignored, `QARAU_RUNS_DIR`) and are never committed; only the code
and these notes are. Results of each refresh: `docs/notes/p3e-refresh-report.md` (P3e). Earlier
rounds wrote `*-2026-10-18`, `*-2026-10-19` and `data/runs/refresh-2026-10-19.json` (the old
`refresh-runs` command, kept as written).

## Viewing the runs

From the repo root (port 5197 as in the tester's setup; any free port works; stop the server
when done):

```sh
cd web && pnpm install --frozen-lockfile && \
  QARAU_RUNS_DIR=/Users/izbassar/Documents/Startup/qarau/data/runs pnpm exec vite --port 5197 --strictPort
```

The Vite dev server serves `/api/runs*` from `QARAU_RUNS_DIR` itself (`web/vite-dev-runs.ts`),
without any auth. Run names the pages must show:

| Page | Login | Shows |
| --- | --- | --- |
| `/viewer/notre-dame-p3e` | none in dev (`RequireAuth allowInDev`) | today's notre-dame run |
| `/viewer/room-p3e` | none in dev | today's room run |
| `/` | required (A1b), see below | all runs: the five `*-p3e` next to the old ones |

`/viewer/notre-dame` and `/viewer/room` are the OLD runs (still in `data/runs`, untouched), not
the refreshed ones; the criterion for this task is met by the dated names above.

**Login on `/`.** `/` always shows the login form until the browser tab has a session
(`web/src/auth`, A1b; dev builds included). With the dev server above, sign in with any
non-empty username and password: the login probe is `GET /api/runs`, which the dev middleware
answers 200 without checking the token. Against the real API instead, the password is the
backend's `QARAU_API_TOKEN` (any non-empty value when it is unset). A scripted browser can skip
the form the way the e2e tests do (`web/e2e/control-center.spec.ts`): before loading the page,
set `sessionStorage['qarau.auth.session'] = 'Bearer dev'` (e.g. Playwright `addInitScript`).

A plain `playwright screenshot <url>/` therefore always captures the login page. To get a
screenshot of `/` with the runs, either the screenshot step signs in first (orchestrator,
`devteam/config.yaml` `shoot`, a protected file), or the web app opens `/` without a session
in dev (`web/src/main.tsx`, the web developer's folder; it is a security-related default, so
it needs the owner's OK). Neither is in this task's folders.
