# A6 — viewer navigation

Extend the existing A2 viewer modules, preserving mouse dragging, collision checks,
damping and the F fly toggle. No runtime dependencies or models are added.

1. Extract a pure wheel classifier with explicit gesture history for stable mouse
   notches. Ctrl-wheel always pinches; other pixel gestures orbit or Shift-pan.
   Document the unavoidable hardware heuristic in an ADR and test the DOM adapter.
2. Scale orbit, pan and zoom by target distance, sensitivity (0.25–4), and Alt
   precision (0.25). Add localized settings persisted separately for each signed-in
   user; server persistence is a follow-up outside this web-only task.
3. Add deterministic low-plane RANSAC, extent-based outer-ground selection,
   ground/scene/distance/pitch constraints, and PCA surface normals as pure math.
   Use the existing Z-up world until P5a supplies gravity. Apply constraints after
   navigation and animations; fly keeps its existing controls plus ground/bounds.
4. Extend presets with front, 3/4 and initial-framing reset. Add an armed surface
   pick, Esc cancellation, local PCA normal and horizon-level alignment.
5. Retain thin camera-path lines, add small dots, and remove all-frame frustums.
   Render only the current-frame frustum. Add Orbit/disabled Walk/disabled Observer
   with 1/2/3 keys, keeping F available for the existing fly controls.
6. Translate all UI text into RU/KK/EN and update the viewer controls design.
7. Run focused tests, then pnpm lint, typecheck, test, build and Playwright smoke.
   Open notre-dame, room and aegviidu without taking screenshots, inspect canvas
   framing/UI/console, and stop every server immediately after verification.

Licence check: installed Three.js, React, Vite and Vitest declare MIT;
Playwright declares Apache-2.0. The i18n system is repository-owned code (no
i18next dependency). All allow the existing commercial use. RANSAC and PCA will
be own code.

## Delivery notes

- Implemented the plan without new dependencies. The existing auth API has shared
  Bearer authentication and no immutable account ID, so successful login usernames
  provide the session preference identity. Backend account IDs and per-user server
  persistence remain a follow-up outside `web/`.
- The existing underside gizmo button is disabled with an explanation. Other axis
  buttons remain available; presets stop at terrain rather than redrawing forever.
- Above-ground cameras may look up at elevated surfaces. Recovery from underground
  removes an upward-facing orientation; orbit/source views always remove roll.
- Local gates: `pnpm lint && pnpm typecheck`, `pnpm test && pnpm build` pass
  (389 tests across 24 files).
  The full suite includes input, speed, constraints, PCA, settings, actual viewer UI,
  point-pick alignment and a single-frustum geometry check.
- Added screenshot-free Playwright checks for `/viewer/notre-dame`, `/viewer/room`
  and `/viewer/aegviidu`, and updated the existing navigation smoke. Attempting the
  existing smoke plus these checks is blocked by sandbox `listen EPERM ::1:5199`;
  an IPv4 loopback probe also returns `EPERM`. Chromium itself is denied by macOS
  MachPortRendezvous permissions. Browser visuals/console therefore need the tester.
- Read-only math preflight on those three real scene arrays found 99.95%, 100%,
  and 100% of sampled points inside the initial view, respectively, with world-up,
  zero horizon tilt (floating-point tolerance), and positive ground clearance.
  Results are in the authorized A6 scratch folder. This uses existing TypeScript
  (Apache-2.0) only to load the source modules in Node.
- No screenshots were taken, and no server or browser process remains from checks.
