# A2a — viewer navigation plan

## Tester follow-up

1. Re-run browser discovery and verification; retain source hashes with screenshots
   so the gate can establish that `/viewer/demo` uses the replacement controls.
2. Replace synchronous large-cloud index construction in the controls constructor
   with a cancellable cooperative build. Yield before work and between bounded
   portions, including cell hierarchy construction. Render the cloud while preparing;
   pause collision-dependent navigation until the complete index is available.
3. Test asynchronous query parity, event-loop yielding, cancellation, and lifecycle
   readiness. Localize the preparation/error status and rerun all required checks.

1. Read the project guide, viewer engine/screen/UI, and 3D viewer design. Replace the
   existing controls without changing point rendering, evidence selection, or video poses.
2. Implement immutable navigation math in `web/src/viewer/navigation.ts`: cursor-ray
   pivot picking, polar-clamped orbit, cursor dolly with an advancing pivot, screen-space
   pan, expanded bounds, swept point collision, exponential damping, and input mapping.
   Index the full cloud in a spatial grid once so interaction avoids scanning all points.
3. Add a DOM-only `NavigationControls` adapter with pointer capture, trackpad/mouse wheel
   mapping, frame-time damping, cancellation/disposal, and explicit synchronization for
   engine camera flights. Pick the pivot only at gesture start; remove double-click input.
4. Integrate into the render loop and preserve toolbar, keyboard, and through-camera
   views. Update the controls specification and existing hints in English, Russian,
   and Kazakh.
5. Test the pure functions and DOM lifecycle, including continuous close zoom, nearest
   collision, frame-rate independence, and gesture mapping. Run lint/Prettier, TypeScript,
   Vitest, and production build. Exercise `/viewer/demo` in a browser and save a screenshot.

## Choices and licences

- Own navigation code; no controls dependency. Installed Three.js, React, and Vitest
  licence files were checked: MIT permits commercial use. Existing Playwright
  (Apache-2.0, also checked) is used only for browser verification.
- Bounds margin is a world-space scalar derived from the cloud diagonal; this also
  gives flat clouds usable vertical room. Collision uses the complete indexed cloud,
  not a downsample that could miss the first obstacle.
- Browser wheel events do not identify the hardware. Ctrl-wheel is pinch zoom;
  Shift-wheel pans; line/page deltas or large vertical integer pixel ticks zoom;
  remaining pixel wheels orbit. Document and test this heuristic.

## Implementation and verification

- Added immutable navigation math and a DOM adapter; replaced the engine's old controls
  and double-click handler. Camera motion uses elapsed seconds, swept point collision,
  and segment bounds clipping. Orbit sweeps short chords along the arc and searches the
  last allowed angle when blocked, preserving its radius and viewing direction.
- The full-cloud grid uses percentile-sized cells and a hierarchy over cell bounds.
  Outliers retain their own cells. No cloud points are discarded. The original
  synchronous 5,000,001-point build took 5.8 seconds in this sandbox; that path is no
  longer used by controls for clouds above 50,000 points. They now build cooperatively,
  yielding before any scan and at an 8 ms work-budget target. Grid population, hierarchy
  sorting and grouping all yield. GC and individual operations can exceed the target;
  it is not a real-time guarantee.
- Follow-up CPU measurement on the same deterministic 5,000,001-point dataset finished
  in 3.50 s across 283 tasks, while a 16 ms heartbeat ran 152 times. Median task work
  was 8.05 ms, p95 12.85 ms and maximum 40.47 ms; warmed collision/pivot queries were
  0.29/0.38 ms. This demonstrates event-loop yielding, not browser rendering speed.
  [Measured results and source hash](a2a-controls/index-benchmark.json) identify the code.
- Rendering starts while the full index is prepared. A localized status replaces the
  controls hint until ready, and a localized error is shown on failure. Navigation is
  paused so picking/collision never use partial data. Disposal cancels queued work;
  stale ready/error completions cannot update an unmounted or replacement viewer.
- Ordinary toolbar flights are collision tested. Explicit source-camera views retain
  the recorded pose/roll, with bounds that include the recorded camera centres.
- Added `/viewer/demo` using the existing local fixture and updated EN/RU/KK hints.
  This alias is retained specifically to make the required acceptance URL independent
  of a backend demo run; real-run loading is unchanged.
- `pnpm lint` (ESLint and Prettier), `pnpm typecheck`, `pnpm test`, and `pnpm build`
  pass (104 tests), including navigation, preparation lifecycle and i18n key parity.
  Playwright discovery lists the navigation scenario; its browser run is blocked below.
  The production build retains
  the existing large-bundle warning.
- Browser verification is blocked in this execution sandbox: Vite cannot bind
  `127.0.0.1:5199` or `::1:5199` (`listen EPERM`), and the installed Chromium exits
  on `bootstrap_check_in ... Permission denied`. No screenshot was produced and no
  visual browser pass is claimed.
- On a browser-enabled runner: `cd web && pnpm exec playwright test e2e/navigation.spec.ts`.
  The scenario opens `/viewer/demo`, exercises drag/trackpad orbit, both pan gestures,
  pinch, extreme zoom and bounds, and writes screenshots to
  `web/test-results/a2a-controls/demo-navigation.png` and `demo-after-navigation.png`.
  The follow-up also fixes the Node ESM JSON import, verifies that Vite serves the
  current source and instantiates `NavigationControls`, and records source/screenshot
  SHA-256 hashes in `provenance.json` only after the full test passes. See
  [browser evidence](a2a-controls/verification.md) for the current blocked run.
