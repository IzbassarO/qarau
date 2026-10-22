# 0013 — Classical sky mask, post-mapping point filter, separate far layer

- Status: accepted (dev team, P3b)
- Date: 2026-10-16

## Context

Owner's review 2026-10-15: long outdoor drone shots (notre-dame, aegviidu) grow a "sky wall":
far, badly triangulated points (clouds, horizon, distant hills) that sit between the default
camera and the structure and hide it. Three causes, three fixes:

1. features are extracted in the sky and matched along the clouds' and horizon's edges;
2. some points survive mapping with almost no parallax or a large reprojection error (the
   GLOMAP join and the bridge removal + global BA of ADR 0011 are not followed by COLMAP's
   own incremental filter);
3. correct but far points (horizon forest, hills kilometres away) are mixed into the object
   cloud and stretch its bounds.

## Decision

### 1. Sky mask before feature extraction — `pipeline/qarau_pipeline/sky.py`

Classical detector, OpenCV + NumPy, no learned model:

- work at ≤ 480 px wide; a pixel is a sky candidate when it is **smooth** (mean Sobel
  magnitude < 12 grey levels in a 9 × 9 window) **and** sky-coloured: bright (HSV V ≥ 170,
  any hue), blue (OpenCV hue 85–135 with V ≥ 90) or overcast grey (S ≤ 40, V ≥ 120);
- keep only candidate components (8-connected) that touch the **top** image border;
- close holes with a 7 × 7 kernel (wires, birds), dilate by 2 px (≈ 4–8 px at full size) so
  features on the horizon edge are not kept;
- safety caps: more than 70 % of the frame, or nothing touching the top → keep the whole frame
  (reason `cap` / `no_sky`, counted in `run.json` → `reconstruction.metrics.sky_mask`);
- masks are written as `colmap/masks/<image name>.png` (COLMAP's naming, 0 = no features) and
  passed via `ImageReaderOptions.mask_path` to every feature extraction (bridges included).

On by default; `qarau_pipeline run --no-sky-mask` restores the pre-P3b behaviour.

**Why no model:** a blue-or-white, untextured region touching the top of a drone or phone
frame is sky in all our footage (checked on all 41 sampled notre-dame frames and 8 aegviidu
frames: the masks cover the sky and stop at the horizon line). A segmentation model adds
weights, a licence review and a GPU dependency for no measured gain. If one is ever needed,
both code and weights must be Apache-2.0, MIT or BSD, recorded here, and cleared with the
owner first. **NVIDIA SegFormer (NVIDIA Source Code License, non-commercial) is forbidden**;
no model weights are referenced or downloaded by this change.

**Licences:** OpenCV (`opencv-python-headless`, Apache-2.0, already a dependency), NumPy
(BSD-3-Clause), pycolmap/COLMAP (BSD-3-Clause). No new dependency.

### 2. Point filter after mapping — `pipeline/qarau_pipeline/point_filter.py`

Run on the final model, before `sparse/0` is written:

- max triangulation angle over all pairs of observing camera centres (tracks longer than 40
  images are subsampled evenly, ends kept) **< 1.5°** → dropped, `points_dropped_low_parallax`;
- else mean reprojection error (recomputed with `update_point_3d_errors`) **>
  `filter_max_reproj_error`** read from pycolmap's mapper options (4 px) →
  `points_dropped_reproj`;
- each point counts in one bucket; `points_total` (before), `points` (kept).

Same thresholds as COLMAP's own incremental filter (`filter_min_tri_angle` 1.5°,
`filter_max_reproj_error` 4 px), applied to whole points at the end, so the GLOMAP join and
the bridge removal cannot leave unfiltered points behind. The three counts are in `run.json`
and in every video of the benchmark `results.json` and table.

### 3. Far layer — `export.py`

A point is **far** when its distance to the **nearest** registered camera centre is more than
**K = 2.5 × the median** of that distance over all points. Nearest camera, not the trajectory
centroid: a long walk along a pipeline keeps the points at both ends. The median, not a high
percentile: a sky wall can be more than 10 % of the points, so a P90 can sit inside it.

K was 4 in the first round; the tester's screenshots still showed a band above the notre-dame
ruin. Distance to the nearest camera / median on notre-dame: P50 1.0, P75 1.37, P90 4.1,
P95 6.9: structure and near ground sit below ~1.5, the band is the hillside at 2.5–4. Rendered
with the viewer's rules (default view, build-up off):

| K | notre-dame far | band above the ruin | aegviidu far | bounds radius nd / ae |
| --- | --- | --- | --- | --- |
| 4 | 4 515 of 43 355 (10 %) | visible | 17 085 of 60 501 (28 %) | 11.3 / 14.1 |
| 2.5 | 6 943 (16 %) | mostly gone | 25 374 (42 %) | 8.4 / 9.2 |
| 2 | 8 241 (19 %) | gone, edges of the near ground start to go | — | 7.2 / — |

K = 2.5: the band goes, the near ground stays, and the smaller bounds also shrink the viewer's
frustums (sized 0.04 × bounds radius). On aegviidu the far layer then holds the village and
forest seen from altitude (4–6 × the close-up distance): context, still in the bundle for A5,
not the inspected tower.

Far points are written as their own arrays (`web/far.position.f32`, `web/far.color.u8`, same
encoding and world frame as the object cloud) under manifest key `far` (ADR 0003 addendum).
The object cloud, its tracks, bounds, `z_range` and `cloud.laz`/`cloud.ply` never contain
them. The viewer (`web/src/lib/bundle.ts`) reads only the keys it knows, so it does not load
`far` until task A5 renders it as a separate, toggleable layer.

## Consequences

- No sky features → no sky points and fewer wrong matches; frames that are mostly sky keep
  every feature (cap), so registration cannot fall below the unmasked run because of the mask.
- `points` in the benchmark is the filtered object model; compare it with `points_total` to
  see what the filter removed.
- Far context (horizon) is still in the bundle for A5, but never changes the default view.
- SIFT rarely fires in a smooth sky, so on our videos the mask removes few features (aegviidu:
  255 of 1.98 M, about the run-to-run noise of extraction itself). It is a cheap guard
  (≈ 2 s per 150 frames) for skies with clouds, haze and compression noise; the sky wall
  the owner saw is mostly far terrain and horizon, which the far layer takes out of the view.

## Measured (quick benchmark, 2026-10-16, `docs/notes/benchmark.md`)

| Video | Registered % | Points total → kept | Low parallax | Reproj. drop | Reproj. px | Far layer |
| --- | --- | --- | --- | --- | --- | --- |
| notre-dame | 100 (dev 100) | 43 298 → 43 298 | 0 | 0 | 0.395 (dev 0.40) | 4 518 of 43 298 |
| aegviidu, run 1 | 100 (dev 100) | 84 915 → 77 545 | 905 | 6 465 | 0.661 (dev 0.533) | 16 202 of 77 545 |
| aegviidu, run 2 | 100 | 61 821 → 60 728 | 1 093 | 0 | 0.532 | 17 530 of 60 728 |
| aegviidu, no mask | 100 | 62 169 → 60 831 | 1 338 | 0 | 0.532 | 17 302 of 60 831 |
| aegviidu, final code | 100 | 62 067 → 60 501 | 1 453 | 113 | 0.525 | 25 374 of 60 501 (K 2.5) |
| notre-dame, final code | 100 | 43 355 → 43 355 | 0 | 0 | 0.394 | 6 943 of 43 355 (K 2.5) |

Rows above "final code" were exported with K = 4.

Final code (ADR 0011 addendum) on aegviidu: merge step 353 s instead of 612–754 s, total
1 346 s (dev 1 329 s) of the 1 800 s per-video budget.

Aegviidu's incremental mapping splits (132 of 150) and the GLOMAP join (ADR 0011) is
accepted. That join varies between runs on near-identical features: run 1 attached frames
106–122 with many points at 1.0–1.5 px per image (6 465 points over 4 px, removed by the
filter), run 2 and the no-mask run reproduce dev (0.53 px). Not caused by the mask: the
masks change 255 features, and the same code with the mask gives 0.532 px again. The
variance of the GLOMAP join is a known risk for the reprojection gate on aegviidu, with or
without this change (ADR 0011 addendum: the BA after the join now runs only when bridges were
removed, with Huber loss).

Why run 1's mean stayed at 0.661 px after the filter dropped 6 465 points over 4 px: the mean
is measured on the filtered model (after `filter_points`, same as the benchmark row), and the
excess is not in a few outliers but spread over the ~17 joined frames, 1.0–1.5 px per image on
thousands of points each. Removing the > 4 px tail lowers the mean only a little; a further
BA on that model reaches 0.62 px (100–300 iterations; a Cauchy loss diverged).

## Default view in the app (finding for dev_app, `web/` is out of this task's scope)

Reproduced offline from the bundles with the viewer's own rules (`engine.ts`, `geometry.ts`):

- The viewer opens at **frame 0 with build-up on** (`ViewerScreen.tsx`), so every point first
  seen after frame 0 is drawn `#2A3240`: **93 % of notre-dame's and 95 % of aegviidu's**
  object points. The structure is there but reads as a dark blob. The design prototype opens
  mid-video (frame 64); opening at the last frame, or with build-up off until play, fixes it.
- Frustums are `0.04 × bounds radius`, and the bounds include every camera centre. The
  viewer's own focus puts the default eye at the notre-dame orbit (a ring of frustums around
  the ruin) and next to aegviidu's vertical climb (frustums across the tower).
- No far band occludes the structure: with the far layer split off (K = 2.5), notre-dame's
  bounds radius drops from 25.6 (dev) to 8.4 and the hillside band above the ruin leaves the
  object cloud. With build-up off, the same renders show the ruin and the tower clearly. A
  `world.focus` written by the pipeline was tried (structure centre, eye inside the camera
  envelope, world yaw towards the cameras): it does not help while 93–95 % of the points are
  in the not-yet-seen colour, so the framing stays the viewer's.
- Framing hints the current viewer honours were evaluated (round 4), rendered with the
  viewer's rules and build-up on: `world.focus` (centre + radius) plus a world yaw about Z
  (the export owns the azimuth) so the fixed default direction looks from the frame-0 side,
  where the frame-0 (bright) points face the viewer. Centres: the viewer's own focus, or frame
  0's optical axis at the median depth of its points; eye at 0.7–1.5 × the frame-0 camera
  distance. Best case (viewer centre, 0.7 ×): the notre-dame frustum ring moves to the top
  edge and the ruin fills the view; on aegviidu the tower fills the view but the drone's
  vertical climb puts frustums across it from that side. In every variant 93–95 % of the
  points stay `#2A3240`: framing cannot change which points frame 0 observed. Not shipped:
  it moves the camera, not the colour, and adds a second copy of the viewer's focus logic.
- Not done on purpose: redefining `first_seen` as "inside frame i's view" would light the
  whole frame-0 field of view, but it would mark unmatched and occluded points as seen,
  breaking ADR 0003's meaning (first observing frame) and blurring it with coverage (trust
  rule: coverage is shown, not assumed).
- **Decision needed (owner):** the P3b criterion "structure clearly visible in the default
  view" cannot be met from the bundle while the viewer opens at frame 0 with build-up on.
  Either (1) a `web/` task for dev_app: open at the last frame, or build-up off until play,
  and frustum size from the point cloud, not the camera envelope; then re-shoot; or (2) waive
  the criterion for P3b and judge "no sky wall occludes the structure" with build-up off.
  This task may only edit `pipeline/`, `backend/`, `infra/`, `docs/`.
