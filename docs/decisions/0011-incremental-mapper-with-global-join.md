# 0011 — Incremental mapper by default; a split video is joined with GLOMAP and bridge frames

- Status: accepted (dev team, P3)
- Date: 2026-10-15

## Context

Three of five benchmark videos came out as several COLMAP models (room 2, aegviidu 3,
mykolaiv 3); only the largest reached the map. On room the cause is a fast pan over a blank
wall: the frame-quality check drops the two blurred frames in the middle of it, and the frames
on either side share **zero** feature matches (`docs/notes/p3-one-model-plan.md`). No mapper,
merger or threshold can join two groups of images that have no correspondence.

pycolmap 4.2.1 (BSD-3, already pinned) ships both COLMAP's incremental mapper and the GLOMAP
global mapper (`pycolmap.global_mapping`). It has no binding for COLMAP's model merger.

## Decision

1. `ColmapReconstructor.mapper = "incremental" | "global"` (CLI `--mapper`), unknown values
   are refused. **Default stays `incremental`**: on single-model videos it is the measured
   baseline (notre-dame 100 %, 0.39 px), and an earlier room experiment with GLOMAP on
   sequential matches triangulated almost nothing (`docs/notes/room-test.md`).
2. When the mapper output is split (or one model misses ≥ 10 kept frames), the stage joins it:
   1. the frames the quality check dropped are added to the database as **bridges** (same
      camera, matched against everything);
   2. one GLOMAP pass maps all images: rotation averaging crosses the fast pan, where
      incremental registration of the bridges finds too few 3D points (measured: incremental
      with the bridges still gives 2 models);
   3. if kept frames are still missing, relaxed leftover registration (continue the best model
      with `abs_pose_min_num_inliers` 15, `abs_pose_min_inlier_ratio` 0.15,
      `max_reg_trials` 10);
   4. the candidate with the most kept frames wins (ties keep the plain mapper output), the
      bridges are deregistered — blurred frames link the map but are never evidence — and a
      global bundle adjustment runs.
3. Models that still stand apart are written to `colmap/sparse/1..`, listed in the metrics and
   counted in `models`; never dropped silently. `--no-merge` restores the pre-P3 behaviour.
4. Loop detection is removed: it needs COLMAP's vocabulary-tree file, whose data licence is
   unclear, and the old code downloaded it at run time. Above 300 frames, sequential matching
   uses overlap 20 with quadratic overlap (pairs i, i + 2^k) to catch start–end loops.

## Consequences

- Room: 2 models / 95.5 % → 1 model / 100 %, +~70 s (bridge matching + one GLOMAP pass).
  Single-model videos pay nothing.
- GLOMAP is not deterministic, even with a fixed seed: on room 1 run in ~13 placed an 8-frame
  segment 2 units (of 10.9) off; the rest agree with the incremental model within 2 % (plan
  note). Watch it; best-of-two joins is the next step if it repeats.
- The join step is a second mapping run; on large drone sets it costs minutes, inside the
  benchmark's 30 min per-video budget but to be watched in the full gate run.
- If a later pycolmap exposes `MergeReconstructions`, merging models that share images can
  replace part of the join without a second mapping run.

## Addendum 2026-10-16 (P3b): BA after the join only when bridges were removed

The GLOMAP variance repeated on aegviidu (ADR 0013): of four runs on the same frames, one
joined the 18 leftover frames with 1.0–1.5 px per image and 6 465 points over 4 px (mean
0.661 px against 0.525–0.532 in the other three and on dev).

- Changed: the global BA after the join runs only when bridge frames were actually
  deregistered, and with the Huber loss GLOMAP's own BA uses. Before, it always ran with
  the default trivial loss: with no bridge in the model it re-adjusted an already adjusted
  GLOMAP model, letting points GLOMAP keeps (its filter allows ≈ 0.01 × focal ≈ 23 px) pull the
  poses. Measured on aegviidu: merge step 612–754 s → 353 s, total 1 346 s, 0.525 px.
- Tried and reverted: relaxed leftover registration into the mapper's own model **before**
  GLOMAP. On aegviidu it reached 134 of 150 frames, so GLOMAP still ran, and the attempt
  added ~140 s to a video already close to the 1 800 s per-video budget.
- Not used: a fixed seed (GLOMAP is not deterministic with one, above; feature extraction
  already differs by a few features per frame between identical runs). Best-of-two joins
  would double the ~600 s join on a video near the budget. Next step if the gate trips on it:
  shorten aegviidu matching (exhaustive 150 frames × 13 k features ≈ 510 s), then best-of-two.
