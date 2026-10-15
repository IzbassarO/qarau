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
| Keyboard (when canvas focused) | `F` frame selection, `Home` reset, numpad-style 1/3/7 front/side/top |

Pick the pivot once at pointer-down: the nearest cloud point within the cursor ray's
pick tolerance. If the ray misses the cloud, use the point along the ray at the current
target distance. A drag keeps this pivot; wheel gestures do not pick another one. No
double-click action is registered.

Orbit preserves the distance to the pivot and clamps the polar angle. Pan follows the
camera's screen axes and scales with distance. Zoom moves towards the cursor ray; close
to the pivot, it advances the pivot along the view ray while keeping a positive camera
distance, so repeated zoom does not get stuck at a minimum orbit distance.

Motion uses frame-rate-independent exponential damping. The camera stays inside the
cloud's bounding box expanded on every side by 50% of its diagonal, and movement stops
before the first nearby cloud point along the path. Spatial indexing keeps picking and
collision checks interactive for large clouds.

For clouds above 50,000 points, prepare the full spatial index in cooperative tasks
after returning control to the browser. The point cloud and the rest of the UI can
render during preparation. Show a localized preparation hint and pause navigation
until the complete index is ready; never use an incomplete cloud for collision checks.
Cancel preparation when the viewer is disposed. Report a failed build in the canvas.

Interactive movement and ordinary toolbar flights use swept point collision. The
explicit video-camera view reproduces the recorded pose and roll exactly; its flight
does not use point collision, because changing the source pose would break video/map
correspondence. Camera bounds still apply and include the recorded camera centres.

Browsers expose trackpad scroll and mouse wheels through the same wheel event. Treat
Ctrl + wheel as a pinch, line/page deltas as a mouse wheel, and a large integral vertical
pixel delta (at least 40, without horizontal movement) as a mouse wheel. Other unmodified
pixel deltas orbit; Shift + wheel pans. This is a documented input heuristic because the
browser does not identify the physical device.

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

## Performance targets

- 5 M points at 60 fps on a 2022 MacBook Air (Potree-style octree LOD, point budget).
- Scrubbing the timeline updates highlights within 50 ms (precompute per-frame visible
  point index lists or a bitset per frame; stream the current frame JPEG).
- First paint of a processed inspection < 3 s on a normal connection.

## Implementation notes

- Three.js with a custom `ShaderMaterial` for points: attributes `height`, `firstSeen`
  (frame index), `findingId`, `neverSeen`; uniforms `currentFrame`, toggles. All state
  changes are uniform updates, not geometry rebuilds.
- Per-frame "visible now" set: a texture or SSBO-free approach — store, for each frame,
  a range into a sorted index buffer and upload a `visibleNow` attribute on frame change
  (or use a bitmask texture lookup in the vertex shader).
- Point picking: GPU picking pass or a KD-tree on the CPU for the clicked ray.
- The video panel shows the **real extracted frame**, not a re-render.
