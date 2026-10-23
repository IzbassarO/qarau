# U2 — video upload in the web app

## Contract

- All requests use the existing bearer session. `GET /api/sites` returns
  `[{id, org_id, name}]`.
- `POST /api/inspections {site_id, mode: quick_scan}` returns
  `{id, site_id, mode, status, created_at}`. File metadata is for the upload session,
  not extra fields on inspection creation.
- `POST /api/inspections/{id}/video {name, size}` returns `{upload_id, run_id}`.
- `PUT /api/inspections/{id}/video/chunks?upload_id=…&offset=…` sends raw bytes with
  `Content-Type: application/octet-stream`; the response is `{next_offset}`.
  `GET` of the same path without `offset` returns the authoritative next offset.
- `POST /api/inspections/{id}/video/complete {upload_id, sha256}` verifies the file,
  returns `{run_id, file, size, sha256}` and marks the run uploaded. A mismatch is 422;
  invalid offsets or a completed session give 409. There is no cancel/delete route.
- B1 source and its OpenAPI route/schema declarations have no MIME allow-list, size
  maximum, or 413/415 branch. Preserve the existing `video/*`, non-empty validation;
  document a frontend 10 GiB safety cap (ADR U2). Handle future server 413/415 too.

## Transfer and UI

Use sequential 4 MiB chunks. A failed chunk (network or 5xx) gets at most three attempts
in total, with 500/1000 ms abortable backoff and an offset GET before each retry. Network
exhaustion is `network`; persistent HTTP errors are `server`. Manual retry keeps the
inspection and upload session and queries the offset again. Cancel/close/unmount abort
requests and backoff, leaving the server partial untouched. Hash incrementally in bounded
memory because B1 requires SHA-256. Progress reports acknowledged bytes and speed over a
configurable five-second moving window with an injectable clock.
If a completion response is lost, manual retry checks the matching run's API status
before repeating B1's non-idempotent completion call.

The list uses `GET /api/inspections` and site names. Detail uses
`GET /api/inspections/{id}` and the existing stage parser. Until S1 supplies statuses,
B1's inspection stays `created`; read `GET /api/inspections/{id}/runs` to display its
sole run's API status and locate a completed viewer bundle. Multiple runs without a
current-run identifier stay unknown: UUID ordering does not identify the newest run.
No inferred processing success. Statuses: uploaded, queued, running, done, failed;
legacy completed maps to done,
everything unfamiliar stays visibly unknown. Poll active inspections. No stages after
upload means “uploaded, waiting for processing”. Refetch and select the inspection after
successful completion. `/?new-inspection` opens the dialog.

Keep the existing neutral shell tokens, Plex typography and compact sidebar rows; add
a quiet status chip and a labelled site selector above the file picker. Upload progress
uses the interface accent and tabular figures. Fit Russian/Kazakh labels in both themes.
All new copy is translated into en/ru/kk. No new dependencies: installed React, Vite,
Vitest, Testing Library and TanStack Query are MIT; TypeScript and Playwright are
Apache-2.0, permitting commercial use. Browser fetch and first-party checksum code only.

## Verification

Vitest: exact chunk offsets/lengths, server-authoritative resume, progress/speed,
cancellation at each boundary, typed errors, dialog states, status chips and API stages.
Playwright: in-memory video against route mocks, progress to 100%, new uploaded sidebar
entry; locale/theme coverage without screenshots. Run pnpm lint, typecheck, test and build.
Kerege runs Playwright outside this sandbox; start no servers here.

## Screenshot environment compatibility

The orchestrator's screenshot command starts Vite with `QARAU_RUNS_DIR` and no API
proxy. That middleware implements only the exported run catalog, so missing CRUD
paths return the SPA HTML document. Detect absent routes (HTML or HTTP 404/405/501)
separately from real network, authentication and server errors. Read the actual
`/api/runs` catalog only when the inspection route is absent; preserve unknown
statuses rather than infer successful processing from a viewer export. Legacy
selections still open their map through their run id without calling CRUD detail.

When sites are unavailable, keep the selector and file picker visible with a calm,
translated availability explanation and a disabled upload action. Do not fabricate
numeric site ids or attempt writes to a server that cannot supply sites. A backend
with B1 enabled continues to load its real sites and supports the complete uploader.
Actual auth/network/5xx errors still show Retry; its button cannot shrink or wrap.
Add regressions for the exact HTML fallback, legacy chips, legacy map selection,
capability versus failure distinctions, and all locale/theme combinations in e2e.
