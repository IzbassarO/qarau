# A9-fix: Observer travel speed

Git history confirms A2b (`e56e45e`) required WASD travel greater than 0.5 scene
units and A9 (`b491363`) lowered that assertion to zero. The 0.5 threshold is
restored. On this checkout it already passes before the movement fix: measured
travel is 2.6945934525186477 units over the test's 20 frames. The reported failure
at that threshold therefore does not reproduce here.

The near-geometry problem does reproduce: nearest-point distance approaches zero,
and A9's minimum speed of 0.5% of the bounding-box diagonal per second barely
moves the camera. A new one-second test with normal damping and a diagonal of 10
travels only 0.04500022699964875 units before the fix, below its 0.5-unit minimum.

Observer now defaults to 6% of the scene diagonal per second as its speed floor.
After acceleration, a one-second hold covers at least 5% of the diagonal in the
tested small and medium scenes. Distance adaptation retains its factor of 0.8;
the upper bound remains half the diagonal per second, capped at 100 scene units
per second for very large scenes. Both default bounds respect that cap. Explicit
speed overrides retain their existing behavior, and boost applies its existing
fourfold multiplier after the bounds. Scene units do not imply metric calibration.

Velocity damping, Orbit navigation and input mappings are unchanged. In current
Observer controls Ctrl/Alt boosts and Shift descends; the legacy fly integrator's
Shift boost is also unchanged. Tests cover scene-relative travel with damping,
distance adaptation, both speed limits in large scenes and exact boost ratios.
The original exact floor assertion remains 0.05 at its original precision, with
an explicit `minSpeed: 0.05` override in that test. A separate test checks the new
default of 0.6 for a diagonal of 10. Input-to-movement tests verify that each
configured Ctrl/Alt boost multiplies speed and travel by exactly four near,
between and at the speed bounds, while Shift descends without boosting. The
plan's Shift-boost wording predates A9's Observer mapping; testing the configured
modifier preserves that mapping. No existing assertion is weakened or removed.

No dependencies or models were added. Installed package metadata confirms
Three.js, React, Vitest, ESLint and Vite use MIT; TypeScript and Playwright use
Apache-2.0. These licences permit commercial use.

Verification: `pnpm lint && pnpm typecheck` and `pnpm test && pnpm build` pass in
`web/` (499 tests across 28 files). The new near-geometry and large-scene tests
failed against the previous movement code and pass after the fix. Diff review
confirms no existing assertions were loosened, removed, skipped or focused.

A browser check without screenshots was attempted for
`/viewer/aegviidu?mode=observer`, but this sandbox rejects listening sockets with
`listen EPERM`. The Vite instance was closed immediately and port 5209 has no
listener. Visual acceptance remains for the tester's screenshot run.

For the tester's Observer screenshot, open `/viewer/aegviidu?mode=observer` and
wait for the point cloud to load. Before capturing, confirm that the Observer
button is pressed, the minimap and Observer controls hint are visible, and no
error banner appears. Keep the page in the foreground: the existing blur handler
exits Observer. The bare `/viewer/aegviidu` URL opens Orbit by design; from there,
click the Observer mode button, or focus the point-cloud canvas and press F, then
capture the resulting Observer view. Page tests cover both the direct link and
the mode-button path. Screenshots remain the tester's responsibility.
