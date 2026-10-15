# A2b — Fly mode and clickable axis gizmo

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

Local checks passed: `pnpm lint && pnpm typecheck`, `pnpm test && pnpm build`.
Tests include the real engine/camera with only the GPU renderer mocked, all six demo
axis views, fly hint translations, pointer-lock/fallback input and mode cleanup.
The production build retains Vite's existing large-chunk advisory.

Browser verification is blocked by this environment. Vite on 127.0.0.1 and localhost
both fail to listen with EPERM. Existing Playwright Chromium also aborts at macOS
MachPortRendezvousServer with permission denied. No screenshots were attempted.
`web/e2e/navigation.spec.ts` provides a browser smoke test for /viewer/demo, six views,
WASD and drag look, without screenshots; it still needs execution outside this sandbox.
