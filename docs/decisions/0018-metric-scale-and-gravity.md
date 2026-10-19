# ADR 0018 — Metric scale from marked points, gravity from cameras + floor

Status: accepted (P5a, 2026-10-18)

## Context

A COLMAP model has an arbitrary scale, position and orientation. Areas in m² (measurement,
change detection "0.2 → 0.4 m²") need metres; the viewer needs the floor or ground level at the
bottom. Phone video has no GPS; drone telemetry and AprilTags (P7) come later.

## Decision

**One similarity per export.** `scale.align()` builds `x' = s·A·x + T` from COLMAP world to the
bundle world: `A` takes up to +Z (the viewer's convention, `world.up = [0, 0, 1]`), `T` puts the
point centroid at x = y = 0 and the floor at z = 0, `s` = metres per COLMAP unit (1 without a
reference). Points (object, far, dense) get `x'`; every camera gets
`R' = R·Aᵀ`, `t' = s·t − R'·T`, so camera coordinates scale by `s` and every projection is
unchanged. `world_from_colmap` stores the full 4×4 (scale included). Export always starts from
the untouched `colmap/sparse/0`, so rerunning `scale` or `export` never compounds. Point counts
do not change.

**Gravity, by default on every export.**
1. Camera estimate: mean of the cameras' image-up axes in world (upright phone or gimbal
   drone; the assumption of COLMAP's `model_orientation_aligner`); degenerate (nadir flight)
   → normal of the camera-centre plane (unchanged from M1).
2. Refinement: NumPy RANSAC plane (500 hypotheses on ≤ 20 000 points, inlier distance 1 % of
   the 2–98 % extent, SVD refit on the inliers). Only planes whose normal is within 30° of the
   camera estimate and that lie below the mean camera height count (floor / ground, not a
   ceiling or a wall); the one with most inliers wins if it holds ≥ 15 % of the points (a slab
   through structureless points holds 3–6 %). Else the camera estimate stays.
3. `world.gravity_source` = `plane` | `cameras`; floor z = 0 is the plane, or the 2nd height
   percentile when there is no plane.

Measured on dev models: room — floor 34 % of points, 4.0° correction, median camera roll
2.3° → 1.2°; notre-dame — ground 53 %, 5.7° correction.

**Scale reference.** JSON version 1, `docs/notes/scale-reference-format.md`: `kind`
`distance` (2 points + `distance_m`) or `a4` (4 corners), each point marked in ≥ 2 registered
frames. Points are triangulated by multi-view DLT on undistorted normalized coordinates
(`Camera.cam_from_img`). `distance`: `s = d / |p1 − p2|`. `a4`: least squares over 4 edges and
2 diagonals (both edge orders tried), `scale_error_cm` = RMS residual of the 6 lengths. The
benchmark's "Scale err cm" column shows it when a manifest entry has `scale_reference`.

Manifest `world` keys: `metric`, `scale_source` (`distance` | `a4` | null), `scale_factor`,
`gravity_source`, `up_axis` (`+z`), `scale_error_cm` (null unless A4).

## Alternatives

- **Cameras only** (M1): biased by the gimbal pitch when the flight does not go all the way
  round, and by how the phone was held. Kept as the estimate and the fallback.
- **Vanishing points / horizon line**: more code, fails indoors and on featureless sky.
- **Gravity from the x-axes of the cameras (zero roll)**: robust to pitch, but degenerate for a
  straight walk; a candidate if the plane fails on real sites.
- **Open3D `segment_plane`**: same RANSAC; NumPy keeps Open3D out of the pipeline's
  dependencies.
- **Pydantic for the reference**: not a pipeline dependency; a small hand-written validator
  gives the same field-named errors.

## Consequences

- The ground of a sloped site is levelled: the map is tilted by the slope (notre-dame's
  hillside: median camera roll 2.4° → 5.4°). Drone telemetry (gimbal, GPS) and P7 AprilTags
  will give true gravity; until then the floor wins because it is what the viewer shows.
- A table top bigger than the visible floor would become "the floor" (height 0 at the table);
  the rotation is still right.
- `distance` has no self-check; a mis-marked point gives a wrong scale silently except for the
  marks' reprojection error (`scale_marks_reproj_px` in the export metrics). A4 reports its
  residual.
- `project` uses 1 m per unit when the bundle is metric, so finding areas come out in m².

Licences: NumPy (BSD-3-Clause), pycolmap/COLMAP (BSD-3-Clause). No new dependency.
