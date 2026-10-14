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
| Left drag | orbit around target |
| Right drag or Shift + drag | pan |
| Wheel / pinch | zoom (dolly towards cursor) |
| Double-click on a point | set orbit target there |
| Toolbar | reset, top, side, view through current video camera |
| Keyboard (when canvas focused) | `F` frame selection, `Home` reset, numpad-style 1/3/7 front/side/top |

Smooth (damped) motion; never lose the object (clamp distance, keep target near data).

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
