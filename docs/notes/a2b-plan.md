# A2b — Fly mode and clickable axis gizmo

## Follow-up validation

The worktree starts with the initial A2b implementation. Audit it against the task,
then fix concrete gaps with regression coverage:

1. Keep all six gizmo buttons clickable when different world axes project to the
   same point in diagonal views, as well as at the axis poles. Test the layout math.
2. Check fly entry/exit orientation, damped movement and input lifecycle against
   the real engine tests; cover any corrections in the pure helpers and page tests.
3. Re-run all required checks and attempt the dev-server/browser smoke test without
   screenshots. Record environmental limits rather than claiming a browser pass.

1. Add pure, tested fly movement and axis-orientation helpers. Reuse navigation's
   bounding-box clamp; keep the engine's +Z world up and scale movement to cloud size.
2. Wire focused-canvas F/Escape, pointer lock with drag fallback, damped movement,
   and a forward orbit pivot on exit. Clean up input and pointer lock on disposal.
3. Replace the rendered axis indicator with six accessible, camera-oriented buttons
   in the bottom-left corner. Animate axis views around the current pivot using
   quaternion slerp and exponential damping while preserving distance.
4. Translate controls and hints in EN/RU/KK; cover pure helpers, engine input, and the
   demo page integration. Update viewer behavior documentation.
5. Run lint (including Prettier), typecheck, Vitest and production build. Exercise
   /viewer/demo with browser keyboard/mouse without taking screenshots.

Licences checked in installed packages: Three.js, React, Vitest and React Testing
Library are MIT (commercial use allowed). Browser Pointer Lock is a platform API.
No new dependencies or model use. Existing Playwright is Apache-2.0.

Task instructions override repository defaults: no commits/branches, no progress.md
edits, no screenshots. All changes stay under web/ and docs/.

## Implementation and verification

Fly and orbit reuse `clampToBounds` and the same Box3/margin. Engine flights also
reuse the swept collision check. Axis views use the current pivot/radius and stable
Z-up pole quaternions. Reset caps its framing distance to a sphere inside the expanded
bounds: the demo's old framing radius put the desired top view beyond the allowed
height, making a bounded, constant-radius top flight impossible. The reset FOV
widens proportionally to preserve the previous framing extent (capped at 120°).

The follow-up fixes two reproducible interaction defects:

- Diagonal views could project two pairs of gizmo buttons onto identical positions,
  completely hiding their rear buttons. The shared projection helper now separates
  endpoints by at least 24 px and keeps all 22 px buttons inside the 104 px widget.
  Leader lines follow their resolved endpoints. Tests cover diagonal coincidences,
  all six poles and 512 rotated/rolled orientations.
- Entering fly from a nearly converged pole view used tiny residual XY components
  to pick an arbitrary yaw and rotated the camera without input. Entry now keeps
  the existing orientation until nonzero mouse look, adopts exact ±90° pitch, and
  derives near-pole heading from camera screen-right. Mouse look still clamps to
  ±89°. Tests cover both poles, first look, re-entry and recorded camera roll.

Final local checks passed: `pnpm lint && pnpm typecheck`, `pnpm test && pnpm build`.
All 167 tests pass, including i18n key parity. Tests retain the real engine/camera
with only the GPU renderer mocked, all six demo axis views, fly hint translations
and axis selection in EN/RU/KK, pointer-lock/fallback input and mode cleanup.
The shared orbit/fly bounds clamp and collision constraints remain in use.
No dependencies changed. The build retains Vite's existing large-chunk advisory.

Browser verification remains blocked by this environment. Direct Vite startup on
127.0.0.1:5199 fails with `listen EPERM`; `pnpm e2e e2e/navigation.spec.ts` likewise
fails before browser launch because its Vite server cannot listen on ::1:5199.
No screenshots were attempted. The smoke test covers /viewer/demo, actual button
hit targets in a diagonal view, six axis flights, WASD and drag look; it still needs
execution outside this sandbox.
