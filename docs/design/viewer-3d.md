# 3D viewer and video ↔ map sync — specification

> **Colours and look: `docs/design/DESIGN.md` (design system v8) wins.** This file describes how
> the viewer behaves. Where it names a colour (black canvas, `#5B9BFF`, `#6B727D`, height-blue,
> red/orange), use the v8 token with that role instead: `map-ground`, `critical`, `watch`,
> `unseen`, `ok`, `map-grid`, `map-text` (owner, 2026-10-24).

The 3D viewer is the core screen of the product. An engineer must be able to fly around
the site like in Blender, and see exactly how the video became the map: which part of the
object each moment of the video saw, and which video moments prove each point and each
finding.

Finding evidence prototype: `prototypes/Finding.dc.html`.

## Layout

- Top bar: site / inspection, tabs **Обзор** | **3D и видео**.
- Left vertical toolbar: reset, top, front, side, 3/4, align to surface, **view through camera**, zoom ±.
- Canvas top-left: Orbit / Walk / Observer mode switch and navigation settings.
- Centre: full-height black WebGL canvas.
- Bottom: video timeline — play/pause, timecode, scrubber; ticks above the scrubber mark
  the video moments where findings are visible (colour = severity).
- Right panel (≈340 px): the **real video frame** at the current time, camera altitude,
  number of points visible in this frame, "map assembled so far" %, display toggles,
  findings list; selecting a finding lists the timecodes where it is visible.

## Camera controls

| Input | Action |
| --- | --- |
| Left drag | orbit around the cloud point under the cursor at pointer-down |
| Shift + left drag or middle drag | screen-space pan |
| Mouse wheel | zoom towards the cursor (including Shift-remapped wheel ticks) |
| Trackpad pinch (Ctrl-wheel) | zoom towards the cursor, even with Shift |
| Two-finger trackpad drag | orbit around the current pivot, never zoom |
| Shift + two-finger drag | screen-space pan |
| Option / Alt | 4× slower orbit, pan and zoom |
| Toolbar | reset initial framing; top, front, side, 3/4; align to surface; current camera; zoom ± |
| Keyboard (canvas focused) | `1` Orbit; `2` Walk and `3` Observer reserved and disabled; `F` fly; `Escape` cancel alignment / exit fly; `Home` reset; `7` top |
| Fly mode | `W/S` forward/back, `A/D` strafe, `Shift` faster; mouse look (drag when pointer lock is unavailable) |
| Axis gizmo (bottom-left) | ±X/±Y/+Z views around the current pivot; underside −Z is disabled by the terrain constraint |

Orbit is the default. Walk (A8) and Observer (A9) remain visibly disabled with localized
“coming soon” tooltips. The existing F fly controls remain available independently.
Keyboard shortcuts ignore editable fields. Losing pointer lock, window focus or page
visibility exits fly and clears held movement keys.

Orbit, pan and zoom slow down near the target. Settings contain a sensitivity slider
from 0.25× to 4× (default 1×); Option/Alt applies an additional 0.25× multiplier.
The setting is stored at `qarau.viewer.sensitivity.<userId>`. The current shared-token
auth system exposes the successful login username as a client session identity;
anonymous viewers use an in-memory default. True server user IDs and per-user server
persistence are follow-ups when backend accounts exist (outside this web-only task).

The current reconstruction convention is +Z world-up. The horizon stays level in
orbit, presets, alignment and source-camera viewing. The camera and target are kept
above the estimated outer ground. Recovery from an underground pose also removes
upward pitch; above-ground cameras may look up at elevated surfaces. Ground
is estimated with seeded RANSAC over the lowest ~20% of points, preferring wide outer
support over a raised interior slab, with a 2nd-percentile fallback. This is provisional
until P5a supplies gravity. The camera stays within the full scene box (including source
camera centres) plus half its diagonal, with zoom distance limited to 0.002–20 times
the robust scene radius. Fly retains A2 orientation and movement with ground, bounds
and swept point-collision checks.

“Align to surface” arms the next point click. The existing point picker selects the
point and PCA over its 30 nearest neighbours estimates the normal. The camera moves
to the normal on the current camera side at its current distance, with a level horizon;
a normal within 10° of up uses top view. Ground/bounds limits take priority. Escape
cancels. A click missing the cloud keeps alignment armed.

Preset flights preserve the current pivot and distance where constraints allow.
Reset uses the initial robust framing from `defaults.ts`. Navigation uses exponential
damping and the existing swept point collisions. A picked pivot stays fixed throughout
a drag; wheel gestures never repick it. No double-click action is registered.

Wheel events do not identify hardware. Ctrl-wheel always pinches; line/page deltas,
large integral single-axis ticks (≥50 px), and confirmed stable mouse-step multiples
zoom. Shift-remapped ticks in X remain mouse zoom. Other pixel deltas orbit or
Shift-pan. History uses only confirmed mouse events, is local to each viewer, and expires
after 250 ms or a trackpad gesture;
see [ADR 0015](../decisions/0015-trackpad-wheel-classification.md) for ambiguity limits.

## Video ↔ map correspondence (the point of this screen)

Data comes straight from reconstruction — nothing is guessed:

- **Camera poses**: COLMAP gives the pose of every registered frame → camera path and
  a frustum for the current frame is drawn exactly where the drone was.
- **Point tracks**: every COLMAP 3D point stores the list of images that observed it.
  This is the ground-truth link between video and map:
  - frame → points: highlight the points observed by the current frame;
  - point → frames: click any point or finding → list of timecodes that saw it; click a
    timecode → jump the video there and fly the 3D camera to that pose.
- For dense clouds (points not in the sparse model), compute visibility by projecting
  points into each frame with a depth test against that frame's depth map.

### Display states (toggles)

1. **Build-up along the video** (default off, opt-in): points first seen _after_ the
   current time are drawn very dark (`#2A3240`) only while this mode is enabled;
   already-seen points use the selected RGB or height-blue colours. Scrubbing shows the
   map assembling itself from the video. With this mode off, future points retain
   their base colours.
2. **What the camera sees now** (default on): points observed by the current frame are
   brightened and slightly larger.
3. **Current camera frustum** (default off, opt-in): one pyramid in `#5B9BFF`
   for the current registered frame only, plus its highlighted dot. Scrubbing replaces
   this geometry; unregistered frames hide it. No all-frame frustum collection exists.
   Pyramid size scales with the scene's robust extent.

- Points never observed by any usable frame are always gray `#6B727D` (coverage rule).
- Camera path: thin line, travelled part solid light and remaining part dashed/dim, with small dots at the registered frame centres.
- An XYZ axis gizmo in the bottom-left corner.

### Findings

- Drawn as numbered markers (red critical, orange watch) once they have been seen.
- Click marker → select; panel shows timecodes; "Open evidence" → finding screen.
- Timeline ticks show where the selected finding (or all findings) appears.

### Colours and object framing

- The display controls include a localized **Point colours** toggle with **Height-blue**
  and **Real colours** states. Real RGB colours are the default when the bundle has RGB;
  height-blue is the fallback without RGB or an explicit user choice. Only explicit
  colour toggles persist in localStorage (`qarau.viewer.colorMode.v2`); opening a viewer
  does not save its default. The old unversioned key is ignored so A4's stored height
  default does not override the new behaviour. Build-up and frustums start off each time
  the viewer is opened and remain available as localized RU/KK/EN toggles.
- Real colours require a complete RGB triplet for every point. When RGB is absent,
  unreadable or incomplete, the toggle remains visible and disabled with a localized
  explanation. The viewer uses height-blue and preserves any saved RGB preference for
  the next compatible bundle. The synthetic demo has no captured RGB.
- Switching swaps two precomputed geometry colour attributes, without reloading points.
  Finding colours stay red/orange and never-observed points stay gray in either mode;
  build-up and current-frame highlighting retain their existing priority.
- These are web display defaults only; the viewer bundle format (ADR 0003) is unchanged.
- Initial and reset views frame point-only 2nd–98th percentile bounds on each axis,
  excluding stray points and camera-path extents. The target is the box centre, and
  the fit accounts for vertical FOV, canvas aspect and a 10% margin. If navigation room
  shortens the distance, FOV is refitted against all eight corners.
- Navigation limits still include every point and registered camera centre, expanded
  by half the full diagonal on each side. Framing does not restrict access to outliers.

## Performance targets

- 5 M points at 60 fps on a 2022 MacBook Air (Potree-style octree LOD, point budget).
- Scrubbing the timeline updates highlights within 50 ms (precompute per-frame visible
  point index lists or a bitset per frame; stream the current frame JPEG).
- First paint of a processed inspection < 3 s on a normal connection.

## Implementation notes

- Three.js with a custom `ShaderMaterial` for points: attributes `color`, `firstSeen`
  (frame index), `fid`, `findingColor`, `visibleNow`; uniforms for the current frame and
  display toggles. Colour mode swaps the existing `color` attribute; display flags use
  uniform updates. Geometry and position buffers are retained.
- Per-frame "visible now" set: a texture or SSBO-free approach — store, for each frame,
  a range into a sorted index buffer and upload a `visibleNow` attribute on frame change
  (or use a bitmask texture lookup in the vertex shader).
- Point picking: GPU picking pass or a KD-tree on the CPU for the clicked ray.
- The video panel shows the **real extracted frame**, not a re-render.
