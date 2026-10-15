# A1a — control center

## Scope and design

Build the existing React app, using `docs/design/README.md`, `viewer-3d.md` and
`prototypes/Inspections.dc.html`: 280 px site sidebar, thin borders, neutral dark
surfaces, black map, blue focus/progress, IBM Plex type, 8–12 px corners, 24–32 px
main spacing. Collapse navigation below 900 px. Preserve the viewer controls (A2).
Local login and detailed job status (A3) remain outside this task.

## Component tree and routes

- Existing QueryClientProvider → BrowserRouter → AppShell
  - Sidebar: brand/home, New inspection, collapsible sites/inspection links, locales
  - Main: breadcrumb, API error/retry, route content
    - `/`: empty state; no fixture fallback
    - `/inspections/:id`: InspectionView header (title/date/status), embedded existing
      ViewerScreen (video frames + 3D map), processing steps from run metadata
    - `/inspections/demo`: explicitly labelled, self-contained synthetic fixture;
      never fetched or included in the real run list
  - NewInspectionDialog: accessible modal, video file picker, MIME and size validation,
    explicit upload-unavailable message
- `/runs` redirects to `/`; `/viewer/:runId` remains available as the standalone viewer.

Use TanStack Query already in the project. Pure grouping supports optional site,
title, date and status metadata; missing fields stay unknown, never inferred from
filenames or browser time. Missing sites use a reserved unassigned bucket translated
at render time. Sort inspections and groups newest first, unknown dates last.

## Consumed API and limitations

Read `backend/app/runs.py`, `auth.py`, and the registered routers in `main.py`.
All run routes require the `viewer` role. An optional configured shared Bearer token
is enforced by the backend; no token entry, persistence or login is added here.
Same-origin fetch preserves the existing deployment authentication contract; HTTP
401 receives a visible translated error. No credentials are written to files.

- `GET /api/runs` → `[{run_id, source: {kind,name,...} | null, stats: {...} | null}]`.
  Current response has **no site, creation date, overall status or live stages**.
  Adapter accepts optional `site: {id,name}` (or `site_id`, `site_name`), `title`,
  `created_at`, `status` and `stages: [{name,status}]` for enriched responses.
  Null/missing date and status are visibly unavailable. Existing `processing_s`
  entries provide elapsed timings, **not proof of stage success**: show status as
  unavailable unless explicitly supplied in `stages`. Fetch failures stay visible.
- `GET /api/runs/:id/manifest.json` → existing version 1/2 Manifest.
- `GET /api/runs/:id/files/:path` → existing viewer arrays and extracted video frames.
  No original video URL is promised: the existing viewer plays extracted frames.

### Request for dev_pipeline: upload contract

No upload/create-run endpoint exists. Add an authenticated operator route, proposed
`POST /api/inspections` with multipart `video` (non-empty `video/*`), optional `site_id`
and explicit `mode: quick_scan | monitoring`. Return `202 {run_id, status: "queued"}`;
validation failures use 400/413/415/422, auth uses 401/403. The resulting run must be
visible from GET /api/runs, including queued/running/failed jobs, site, created_at,
overall status and stage states. Agree the final path/role before integrating.
Until then the modal validates locally and visibly explains why uploading cannot
start; no requests are fabricated. This request is recorded here instead of
`docs/notes/progress.md`, which the task explicitly prohibits editing.

## Verification and licences

Add grouping/unassigned/sorting tests, sidebar navigation/modal tests, invalid file
and empty file tests, locale key parity, and route/error/demo isolation coverage.
Run pnpm lint, typecheck, test and build. Run Vite and Playwright with screenshots of
home and demo in English and Russian, plus responsive navigation checks. Store
screenshots and a review packet under docs/notes/a1a-control-center/ for the
orchestrator's PR; do not commit, push or create a branch.

Existing installed package metadata verifies React, React DOM, React Router,
TanStack Query, Three.js, Vite, Vitest and Tailwind CSS (MIT); Playwright (Apache-2.0)
and IBM Plex fonts (OFL-1.1). These permit commercial use. Check and note licences
for any added test dependencies before installing. No models or paid services used.

## Implemented and verified (2026-10-15)

All four shell components and the three requested routes are implemented. The existing
standalone viewer remains at `/viewer/:runId`; `/runs` redirects home. The embedded
viewer hides only its duplicate header and resolves the demo illustration locally;
its camera/video controls and engine are unchanged. The shell adapts the embedded
panels for narrow screens. Active navigation preserves failed/unknown status colours.

Added test-only dependencies: @testing-library/react 16.3.3, @testing-library/dom 10.4.2
and jsdom 30.0.1; verified MIT licences in their package metadata. The lockfile is
updated and a frozen, offline install succeeded using a temporary copy of local
package caches. No shared cache, credentials or project security defaults were changed.
jsdom's dialog top-layer behaviour is shimmed only in the test setup; production uses
the browser's native modal dialog for focus trapping and Escape cancellation.

Passed `pnpm lint`, `pnpm typecheck`, `pnpm test` (31 tests across 6 files),
`pnpm build`, and `git diff --check`. Tests cover grouping, null metadata, date sorting,
key parity, active navigation, collapse/expand, dialog opening, invalid and empty files,
RU/KK validation, real-run bundle integration, API errors/retry/authentication, and
isolation of demo data from home/real inspection routes.

Browser verification is **not completed**: `pnpm dev --host 127.0.0.1 --port 5199
--strictPort` fails with `listen EPERM`. `pnpm e2e control-center.spec.ts` fails when its
Vite server attempts to listen on `::1:5199`. An independent Playwright Chromium launch
also terminates inside this sandbox. No screenshots were produced or inspected.
`web/e2e/control-center.spec.ts` contains the four requested EN/RU screenshots, upload
validation and a 390 px navigation/overflow check for a browser-enabled tester to run.
A review packet with the command is in `a1a-control-center/review.md`.

Remaining: execute that browser scenario and attach the resulting screenshots to the
orchestrator's PR; dev_pipeline must supply the upload/enriched-run contract above.
`backend/`, `pipeline/`, `infra/`, `data/` and `docs/notes/progress.md` are untouched.
No commits, branches, pushes or PRs were created, as explicitly required by this task.
