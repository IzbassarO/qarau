# Phase 1 — video → 3D map

Owner's plan, 2026-10-14. Effort split: **~70 % core** (`dev_pipeline`: `pipeline/`,
`backend/`, `infra/`) and **~30 % a thin, polished app shell** (`dev_app`: `web/`, following
`docs/design/`). Executed by the dev team in `devteam/`; tasks live in `devteam/backlog.yaml`
(core tasks are smaller and more numerous, so the brain keeps the core developer busier).

## Core (dev_pipeline)

**P1 Benchmark.** One command runs all reference videos — the owner's room video (when added
to `data/samples/`) plus the 4 public videos — and writes a table: registered frames %,
number of models, reprojection error, points, scale error, time. Results are stored per
commit, so every change to the pipeline is measured against the previous one.

**P2 Robust phone import.** HEVC, HDR → SDR, rotation metadata, variable frame rate;
parallax-based keyframes instead of a fixed rate; a footage quality report with plain
re-shoot advice (blur, too fast, pure rotation, low texture).

**P3 One connected model per video.** Loop detection, global mapper, model merging, so a
video gives one map instead of several pieces.

**P4 Dense reconstruction.** The model licence must allow commercial use. Keep the
frame ↔ point links (each dense point knows which frames saw it) — needed for evidence.

**P5 Metric scale and gravity alignment.** Now: an A4 sheet or one user-measured distance;
the floor/gravity direction is "down". Later-ready: drone telemetry (per-frame GPS, DJI SRT).

## App shell (dev_app)

**A1 Control center**, like ChatGPT / Claude: a left sidebar with sites and inspections
(like chats), a "New inspection" button = video upload, the main area = the selected
inspection (video, 3D map, processing steps). A simple local login for now.

**A2 Blender-like 3D navigation** (replaces the current controls), a reusable module with tests:
- orbit pivot = the point under the cursor at mouse-down; no double-click;
- zoom toward the cursor that never gets stuck: close to the pivot, the pivot moves forward
  with the camera (dolly), so one can fly closer and closer;
- the data is the limit: the camera stays inside an expanded bounding box of the points and
  stops at the nearest points instead of passing through empty space beyond them;
- pan with Shift+drag / middle mouse; Mac trackpad: two-finger orbit, pinch zoom,
  Shift+two-finger pan;
- fly mode (WASD + mouse look, Shift = faster), toggled with a key;
- clickable axis gizmo for top / front / side views, as in Blender;
- smooth damping everywhere.

**A3 Processing status view**: clear steps, human-readable errors.

**A4 Colours**: real colours ↔ height-blue toggle; the initial view frames the object.

## Done for Phase 1

A phone video uploaded in the app becomes one connected, dense, metrically scaled map with a
quality report, measured by the benchmark on every commit, viewable with the new controls.
