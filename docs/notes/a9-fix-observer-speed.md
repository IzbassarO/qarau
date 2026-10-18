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
The existing exact floor expectation increases from 0.05 to 0.6 at the same
precision; no existing assertion is weakened or removed.

No dependencies or models were added. Installed package metadata confirms
Three.js, React, Vitest, ESLint and Vite use MIT; TypeScript and Playwright use
Apache-2.0. These licences permit commercial use.

Verification: `pnpm lint && pnpm typecheck` and `pnpm test && pnpm build` pass in
`web/` (495 tests across 28 files). The new near-geometry and large-scene tests
failed against the previous movement code and pass after the fix. Diff review
confirms no existing assertions were loosened, removed, skipped or focused.

A browser check without screenshots was attempted for
`/viewer/aegviidu?mode=observer`, but this sandbox rejects listening sockets with
`listen EPERM`. The Vite instance was closed immediately and port 5209 has no
listener. Visual acceptance remains for the tester's screenshot run.
