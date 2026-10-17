# A7 — region of interest

Read first: `CLAUDE.md`, viewer engine/navigation/input/gizmo, ViewerScreen/UI,
bundle/inspections and route adapters, backend runs/auth/settings, and the Vite
catalog middleware. The current catalog is file based (`QARAU_RUNS_DIR`);
the viewer uses Z-up world coordinates and the gizmo defines stable pole views.

1. Define and document version 1 world-axis-aligned ROI JSON and the per-run
   reconstruction-frame caveat. An unassigned viewer uses its route run id as
   site id; assigned inspections share their actual site id.
2. Add pure, validated drag/box/point-mask/serialization helpers and unit tests.
3. Add authenticated ROI fetch/save/delete clients and explicit errors; connect
   site resolution to the catalog instead of grouping all unassigned runs.
4. Add server file persistence to the existing **development** catalog adapter,
   with validation, safe ids/paths and atomic replacement. Tests use temporary
   catalog directories outside the worktree. No files under `data/` are written
   during verification.
5. Integrate a toolbar tool, exact gizmo top view, exclusive drag input, Escape,
   live rectangle, neutral wireframe, and non-destructive shader clipping of
   object and far points. Load saved site regions, save/clear explicitly, and
   translate status/errors/frame warnings in EN/RU/KK.
6. Run lint/Prettier, typecheck, Vitest and build; exercise drag, clipping,
   save/reload through a short-lived browser session without screenshots. Stop
   every server immediately afterwards and place scratch output only in the
   designated A7 scratch directory.

## Scope constraint

The task also requests `backend/app/roi.py`, registration in `main.py`, and
`backend/tests/test_roi.py`, but the owner's final rules allow edits only to
`web/` and `docs/` and state that other edits are reverted. Expansion to
`backend/` has been requested. Until explicitly granted, do not edit it or claim
the development adapter provides production FastAPI routes. Document the exact
production API contract and remaining work. No pipeline changes.

## Licences

No dependencies or models added. Installed Three.js (`LICENSE`), React
(`LICENSE`) and Vitest (`LICENSE.md`, core MIT with commercially usable bundled
MIT/BSD/ISC components) were checked locally; commercial use is permitted.
Use existing Node built-ins and Vite (MIT) for the development catalog adapter.

## Verification and remaining boundary

- `pnpm lint`, `pnpm typecheck`, `pnpm test` (540 tests across 29 files), and
  `pnpm build` pass. The build retains the existing large-chunk advisory.
- Existing backend `ruff check`, strict `mypy app`, and `pytest` pass (10 tests).
  These do not test production ROI routes: those routes and their Pydantic/pytest
  coverage cannot be added within the authorized edit boundary.
- Viewer integration tests exercise `/viewer/notre-dame` with the real engine
  and mocked GPU/API: reversed top-view drag, save/reload, restored outline,
  source-buffer preservation, clipping, clear, Escape, visible errors/retry,
  cross-run warning, and EN/RU/KK controls. Middleware tests exercise real files
  across fresh handler instances, authentication, invalid bodies and traversal.
- A real Notre-Dame browser check was prepared using a scratch-only catalog and
  read-only sample-run symlink. Starting Vite on `127.0.0.1:5207` failed with
  sandbox `EPERM` before listening, so real-browser rendering/save/reload remains
  unverified here. No screenshots were attempted; they belong to the tester.
  The child exited and a listening-port check confirmed no server on that port.
