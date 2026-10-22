# 0005 — Viewer rendering: one GL_POINTS draw call with a custom shader; LOD deferred

- Status: accepted
- Date: 2026-10-14

## Context

The design asks for 5 M points at 60 fps on a 2022 MacBook Air and for all video ↔ map
states (build-up, "seen now", never seen, findings) to be switchable without rebuilding
geometry. Potree-style octrees give LOD and streaming but add a converter in the pipeline,
a loader in the viewer and complexity in per-point attributes.

## Decision

- One `THREE.Points` with a `ShaderMaterial`; per-point attributes `position`, `color`,
  `firstSeen`, `visibleNow`; all display states are uniforms. Only `visibleNow` is
  re-uploaded when the current frame changes.
- Plain three.js (no react-three-fiber): the canvas is one imperative render loop; React
  owns the panels.
- `OrbitControls` (damping, zoom to cursor) extended with Shift-pan, double-click
  re-targeting, keyboard views and "view through the video camera".
- No octree/LOD in M1. Trigger for adding Potree (or a custom octree): a real run whose
  cloud exceeds ~5 M points or drops below 30 fps in the measured test
  (M1 results removed, see git history: 8be36b3).

## Consequences

- Fast to build and to reason about; everything the engineer sees is a uniform or an index
  slice from the COLMAP tracks.
- Memory is linear in point count (~20 bytes/point); first paint waits for the whole
  cloud. Acceptable up to the measured ceiling, after which LOD is added behind the same
  bundle format.
