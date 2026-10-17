# 0015 — Parallax keyframes: residual after a homography, threshold fitted to the frame budget

- Status: accepted (dev team, P2b)
- Date: 2026-10-17

## Context

The frames stage took `clamp(duration·6, 60, 300)` frames on a fixed clock. A still camera
or a turn on the spot fills that budget with frames that add no 3D geometry, while fast
parts get too few. Measures of "enough camera motion" considered:

- raw optical flow: large for pure rotation too, which gives no depth;
- the median residual of KLT tracks after the best RANSAC homography: ~0 for a still camera
  and for pure rotation (both are exactly a homography), grows with translation over a
  scene with depth. This is what SfM needs;
- essential-matrix / triangulation angle: needs intrinsics we do not have before COLMAP.

On the reference videos the residual per 1/6 s at 640 px is tiny and differs 4× between
videos (notre-dame 0.0018 of the diagonal, room 0.0011, aegviidu 0.0004; still camera
~5e-5). A fixed threshold either keeps every frame (0.0003) or thins notre-dame to 40 of
109 frames (0.005), which loses > 10 % of the points and fails the merge gate.

## Decision

1. KLT tracks (`goodFeaturesToTrack` + pyramidal LK, forward-backward check) over 1.5× the
   budget of candidate frames. A new keyframe when, since the last keyframe: the median
   residual after the RANSAC homography ≥ θ · diagonal; or < 60 % of its tracks survive
   (turning on the spot: at most every 0.3 s); or 2 s passed while the image moved ≥ 1 % of
   the diagonal (a still camera adds nothing).
2. θ starts at the floor 0.0003 and is raised by log bisection until the keyframes fit the
   budget: COLMAP gets the same number of frames as before, placed where the camera moves.
3. A budget already below 6 fps (long videos, `--max-frames`) runs the fixed grid: there
   moving frames out of slow parts cost accuracy (orono reprojection 0.57 → 0.67 px).
4. `--keyframes fixed` keeps the old behaviour. All thresholds are `KeyframeParams` fields.

## Consequences

- notre-dame: +15 % points at the same 100 % / 1 model; room within run-to-run spread
  (`docs/notes/p2b-plan.md`).
- A flat surface filmed head-on moves like a homography, so it gets few keyframes (track
  loss keeps overlap) and may be reported as `pure_rotation`.
- One KLT pass over ~1.5× the frames: a few seconds per video, shared with the quality
  report.
