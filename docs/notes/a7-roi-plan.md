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
4. Add the production FastAPI ROI router with a Pydantic v2 document, explicit
   viewer/editor dependencies, safe catalog paths and atomic file replacement.
   Register it in the API and allow authenticated JSON PUT/DELETE through CORS.
   Keep the development catalog adapter compatible. Tests use temporary catalog
   directories outside the worktree; verification never writes under `data/`.
5. Integrate a toolbar tool, exact gizmo top view, exclusive drag input, Escape,
   live rectangle, neutral wireframe, and non-destructive shader clipping of
   object and far points. Load saved site regions, save/clear explicitly, and
   translate status/errors/frame warnings in EN/RU/KK.
6. Run lint/Prettier, typecheck, Vitest and build; exercise drag, clipping,
   save/reload through a short-lived browser session without screenshots. Stop
   every server immediately afterwards and place scratch output only in the
   designated A7 scratch directory.

## Returned-work fixes

The tester identified missing production API routes and an undiscoverable ROI
control. The owner's follow-up explicitly authorizes the required `backend/`
implementation and tests. Add the FastAPI router and regression coverage; replace
the icon-only ROI entry with a clearly labelled toolbar control and expose the
ROI panel initially so the tool is visible on `/viewer/notre-dame`. Check that
the route and generated application bundle include these controls. Add a browser
regression for drawing, outline, clipping and save/reload against the actual API
where the environment permits listening sockets. No pipeline changes.

## Licences

No dependencies or models added. Installed Three.js (`LICENSE`), React
(`LICENSE`) and Vitest (`LICENSE.md`, core MIT with commercially usable bundled
MIT/BSD/ISC components) were checked locally; commercial use is permitted.
Use existing Node built-ins and Vite (MIT) for the development catalog adapter.
The installed FastAPI 0.143.0 and Pydantic 2.14.1 package metadata both declare MIT;
the production router adds no dependencies.

## Verification after returned-work fixes

- `pnpm lint`, `pnpm typecheck`, `pnpm test` (543 tests across 29 files), and
  `pnpm build` pass. The build retains the existing large-chunk advisory.
- Backend `ruff check`, strict `mypy app tests`, and `pytest` pass (73 tests,
  including 62 production ROI cases). Coverage includes persisted round trips,
  missing ROI, malformed input, authentication for all three methods, traversal,
  symlinks, atomic replacement and failed writes, deletion, and edit CORS.
- Viewer integration tests exercise `/viewer/notre-dame` with the real engine
  and mocked GPU/API: reversed top-view drag, save/reload, restored outline,
  source-buffer preservation, clipping, clear, Escape, visible errors/retry,
  cross-run warning, and EN/RU/KK controls. Initial-visibility assertions require
  the text-labelled toolbar button and open ROI panel without clicking to reveal
  them. Development middleware tests also exercise actual catalog files.
- The screenshot gate launches source Vite from the task worktree and rejects
  occupied ports. The source entry routes to this `ViewerScreen`. A fresh
  production build was inspected and contains the ROI toolbar label, draw label,
  outline, and site API client. It is not an older viewer implementation.
- `e2e/roi.spec.ts` adds a real-mouse/WebGL regression over the actual Notre-Dame
  bundle, with ROI API responses kept in memory so it never edits sample data.
  Its explicit run was blocked before testing: Vite could not listen on
  `::1:5207` (`EPERM`); a separate bind check on `127.0.0.1` has the same sandbox
  restriction. The browser scenario remains unverified in this environment.
  No screenshots were attempted. No server remained listening after the check.
