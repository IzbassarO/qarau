# 3D viewer and video ↔ map sync — specification

The 3D viewer is the core screen of the product. An engineer must be able to fly around
the site like in Blender, and see exactly how the video became the map: which part of the
object each moment of the video saw, and which video moments prove each point and each
finding.

Interactive prototype: `docs/design/prototypes/Viewer3D.dc.html` (open the logic in the
`<script type="text/x-dc">` block — the visibility, frustum and projection math there is a
reference, not production code). Overview screen: `prototypes/Main.dc.html`. Finding
evidence: `prototypes/Finding.dc.html`.

## Layout

- Top bar: site / inspection, tabs **Обзор** | **3D и видео**.
- Left vertical toolbar: reset view, top view, side view, **view through camera**, zoom ±.
- Centre: full-height black WebGL canvas.
- Bottom: video timeline — play/pause, timecode, scrubber; ticks above the scrubber mark
  the video moments where findings are visible (colour = severity).
- Right panel (≈340 px): the **real video frame** at the current time, camera altitude,
  number of points visible in this frame, "map assembled so far" %, display toggles,
  findings list; selecting a finding lists the timecodes where it is visible.

## Camera controls (Blender-like)

| Input | Action |
| --- | --- |
| Left drag | orbit around the cloud point under the cursor at pointer-down |
| Shift + left drag or middle drag | screen-space pan |
| Mouse wheel or trackpad pinch | zoom towards the cursor |
| Two-finger trackpad scroll | orbit around the current pivot |
| Shift + two-finger scroll / wheel | screen-space pan |
| Toolbar | reset, top, side, view through current video camera |
| Keyboard (when canvas focused) | `F` toggle fly, `Escape` exit fly, `Home` reset, numpad-style 1/3/7 front/right/top |
| Fly mode | `W/S` forward/back in the viewing direction, `A/D` strafe, `Shift` faster; mouse look (drag if pointer lock is unavailable) |
| Axis gizmo (bottom-left) | Click ±X/±Y/±Z for right/left/back/front/top/bottom around the current pivot |

The viewer uses +Z as world-up. Axis flights preserve pivot distance, use quaternion
slerp with exponential damping, and choose a stable screen-up at the top/bottom poles.
The six red/green/blue axis buttons rotate with the camera and have localized accessible
labels. Existing top/side toolbar buttons use the same flights.
Projected buttons are spread apart when axes overlap in diagonal or pole views, so
all six directions remain clickable; connecting lines follow the adjusted positions.

Fly speed is 15% of the cloud bounding-box diagonal per second, with a 4× Shift boost.
Velocity and mouse look use exponential damping. Fly shares orbit's expanded bounds
and swept point-collision constraint. Leaving fly keeps the camera pose and places
the orbit pivot ahead of it at the previous pivot distance. F does not repeat-toggle;
keys are ignored in editable controls. Losing pointer lock, window focus, or page
visibility exits fly and clears held movement keys. A localized hint describes the
controls while fly is active.
Entering fly preserves the camera orientation until the first mouse-look input,
including top/bottom views and recorded camera roll. At the poles, mouse look uses
the camera's existing screen-right direction to choose a stable yaw.

Pick the nearest cloud point within the cursor ray's tolerance once at pointer-down.
If the ray misses, use the ray point at the current target distance. A drag keeps this
pivot; wheel gestures do not pick another one. Register no double-click action.

Orbit preserves the distance to the pivot and clamps the polar angle. Pan follows the
camera's screen axes and scales with distance. Zoom moves towards the cursor ray; close
to the pivot, it advances the pivot along the view ray while keeping a positive camera
distance, so repeated zoom does not get stuck at a minimum orbit distance.

Motion uses frame-rate-independent exponential damping. The camera stays inside the
cloud's bounding box expanded on every side by 50% of its diagonal, stopping before the
first nearby cloud point along the path. A spatial index accelerates picking/collision.
Toolbar flights use collision checks; the explicit video-camera view retains its
recorded pose and roll. Bounds include recorded camera centres.

Wheel events do not identify the device. Heuristic: Ctrl + wheel pinches; line/page
deltas or integral vertical pixel deltas ≥40 without horizontal movement zoom;
other unmodified pixel deltas orbit. Shift + wheel pans.

## Video ↔ map correspondence (the point of this screen)

Data comes straight from reconstruction — nothing is guessed:

- **Camera poses**: COLMAP gives the pose of every registered frame → camera path and
  frustums are drawn exactly where the drone was.
- **Point tracks**: every COLMAP 3D point stores the list of images that observed it.
  This is the ground-truth link between video and map:
  - frame → points: highlight the points observed by the current frame;
  - point → frames: click any point or finding → list of timecodes that saw it; click a
    timecode → jump the video there and fly the 3D camera to that pose.
- For dense clouds (points not in the sparse model), compute visibility by projecting
  points into each frame with a depth test against that frame's depth map.

### Display states (toggles)

1. **Build-up along the video** (default on): points first seen *after* the current time
   are drawn very dark (`#2A3240`), already-seen points in the height ramp. Scrubbing
   shows the map assembling itself from the video.
2. **What the camera sees now** (default on): points observed by the current frame are
   brightened and slightly larger.
3. **Camera frustums along the path**: a small pyramid every N frames; the current camera
   always drawn as a larger pyramid in `#5B9BFF`.
- Points never observed by any usable frame are always gray `#6B727D` (coverage rule).
- Camera path: travelled part solid light, remaining part dashed and dim.
- An XYZ axis gizmo in the bottom-left corner.

### Findings

- Drawn as numbered markers (red critical, orange watch) once they have been seen.
- Click marker → select; panel shows timecodes; "Open evidence" → finding screen.
- Timeline ticks show where the selected finding (or all findings) appears.

### Colours and object framing

- The display controls include a localized **Point colours** toggle with **Height-blue**
  and **Real colours** states. Height-blue is the default; explicit choices persist in
  localStorage (`qarau.viewer.colorMode`) for this browser user's viewer session.
- Real colours require a complete RGB triplet for every point. When RGB is absent,
  unreadable or incomplete, the toggle remains visible and disabled with a localized
  explanation. The viewer uses height-blue and preserves any saved RGB preference for
  the next compatible bundle. The synthetic demo has no captured RGB.
- Switching swaps two precomputed geometry colour attributes, without reloading points.
  Finding colours stay red/orange and never-observed points stay gray in either mode;
  build-up and current-frame highlighting retain their existing priority.
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
