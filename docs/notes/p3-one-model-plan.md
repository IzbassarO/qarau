# P3 — one connected model per video

Goal: one video gives one connected COLMAP model. Baseline (a8efcc9, full gate run): room
2 models / 95.5 %, aegviidu 3, mykolaiv 3, notre-dame 1 / 100 %.

## Plan

1. Quick benchmark (`--only notre-dame,room`) on the current code; keep the room database and
   map it again in a scratch script with every sub-model kept, to see why it splits.
2. `ColmapReconstructor.mapper = "incremental" | "global"` (unknown values refused). pycolmap
   4.2.1 (COLMAP 4.x) ships `global_mapping` (GLOMAP, BSD-3) — no new dependency.
3. Matching: exhaustive ≤ 300 frames (unchanged); above, sequential with overlap 20 +
   quadratic overlap. Loop detection is dropped: it needs COLMAP's vocabulary tree, whose data
   licence is unclear, and the old code downloaded it at run time.
4. After mapping, if several models exist: keep the largest, continue it with
   `incremental_mapping(input_path=largest)` under relaxed absolute-pose thresholds so the
   leftover images (including the other models' images) get registered into it, then one
   global bundle adjustment. Accept the result only if it registers more images.
5. Report the final model count and the attempts in the stage metrics; models that still
   stand apart are written to `colmap/sparse/1..` and listed, never dropped silently.
6. Tests: pure selection/merge/count functions, mapper validation, leftover registration
   with a stubbed pycolmap.

## Findings: why room splits

Quick benchmark on the unchanged code (2ccf7f2): room 269 kept frames, 2 models, 257
registered (95.5 %), 366.7 s; notre-dame 1 model, 100 %, 0.394 px, 154.8 s.

Mapping the room database again with every model kept (scratch script, not in the repo):

| Model | Images | Frames | Points | Reproj. px |
| --- | --- | --- | --- | --- |
| 0 | 257 | kept frames 12–268 (all of them) | 26 690 | 0.93 |
| 1 | 12 | kept frames 0–11 (first 2 s: shelves, door) | 2 066 | 0.57 |

- The models share **no** image, and among the 3 084 verified pairs between the two groups
  **none** has a single inlier. The consecutive pair at the cut (video frames 11 → 14) has 0 raw
  matches; neighbouring pairs have 85–370.
- Video frames 12 and 13 were dropped by the quality check (blur 7.4 and 10.3, below 0.35 ×
  the video's median sharpness). The camera pans fast over a plain wall; frames 11–14 show only paint, a bag
  and a socket. Put back, they link the two groups: 11↔12 45 inliers, 12↔13 44, 13↔14 58.
- So this is not weak matching or a missing loop closure: the dropped frames were the only
  link. Global mapper, merger or relaxed thresholds cannot join images without
  correspondences. Exhaustive matching was already used (≤ 300 frames), so the matching
  window was not the problem either.
- With the dropped frames added, incremental mapping **still** gives 2 models (frame 12 never
  registers: a pan gives the bridges almost no triangulated points to localize against).
  Relaxed continuation of the main model with the bridges: all 14 bridges registered, frames
  0–11 still not. GLOMAP on the same database: **1 model, all 283 images**, 27 251 points,
  0.92 px, 49 s.

## Strategy chosen (ADR 0011)

Default mapper stays incremental. On a split: add the dropped frames as bridges → one GLOMAP
pass over everything → relaxed leftover registration if kept frames are still missing → keep
the candidate with the most kept frames → deregister the bridges → global BA. Models that
still stand apart go to `colmap/sparse/1..` and count in `models`.

pycolmap 4.2.1 (`COLMAP 4.x`) facts: `global_mapping` (GLOMAP, BSD-3) is there;
`incremental_mapping(input_path=...)` continues an existing model; no `MergeReconstructions`
binding (the CLI `model_merger` is not used: the product path does not depend on the
binary). `ImageReaderOptions.existing_camera_id` keeps the bridges on the video's single
camera. A second `match_exhaustive` call only matches the new pairs (0.06 s on a rerun).

## Before / after (quick benchmark, `--record`, rows in `docs/notes/benchmark.md`)

| Video | Run | Registered % | Models | Reproj. px | Points | Time s |
| --- | --- | --- | --- | --- | --- | --- |
| room | a8efcc9 (gate) | 95.5 | 2 | 0.93 | 26 706 | 464.9 |
| room | 2ccf7f2 before, this machine | 95.5 | 2 | 0.93 | 26 676 | 366.7 |
| room | **P3** (2ccf7f2+dirty) | **100.0** | **1** | 0.936 | 26 482 | 348.3 |
| notre-dame | a8efcc9 (gate) | 100.0 | 1 | 0.395 | 43 264 | 184.4 |
| notre-dame | 2ccf7f2 before, this machine | 100.0 | 1 | 0.394 | 43 363 | 154.8 |
| notre-dame | **P3** (2ccf7f2+dirty) | 100.0 | 1 | 0.394 | 43 170 | 133.6 |

Room P3 stage metrics: mapper output 3 models this time (3 / 257 / 12 images; incremental
mapping is not deterministic), merge candidates `mapped` 257 → `global_join` 269 kept frames,
14 bridges used then deregistered; seconds: features 54, matching 114, mapping 57, merge 68.
Notre-dame never enters the merge path (one model, nothing left over).

Time is below the old row because time varies run to run on this machine (other runs in
parallel); the merge step itself costs ~70 s on room.

## Is the joined room one room?

Checked on the sparse model (no screenshots; the tester screenshots the viewer):

- Top and side views of the cloud with camera centres: one trajectory; frames 0–11 continue
  its start across the gap left by the two deregistered bridges. No second copy of the walls.
- Aligning the P3 model to the old 257-frame model through their 257 shared cameras (Sim3):
  median camera offset 0.13 units for a 10.9-unit trajectory. One segment (frames 113–120)
  sits up to 2.2 units off, with fewer observations (59–108 vs 132–206 per frame) and higher
  error: a weaker GLOMAP solution there.
- GLOMAP is not deterministic, even with a fixed `random_seed` (multi-threaded). Nine more
  GLOMAP runs on the same database: all 283 images, median offset to the old model 0.010–0.013,
  max 0.05–0.22 (≤ 2 % of the extent), 0.92–1.02 px; removing the bridges and the global BA
  change nothing measurable (max 0.06). So the benchmark run hit a weaker solution about
  1 time in 13.

## Known issues / next

- GLOMAP's spread (above). If it shows up on other videos, run the join twice and keep the
  one with more observations, or refine it with an incremental continuation.
- aegviidu and mykolaiv (3 models each) were not run here (quick set only); the merge gate's
  full run measures them. Their split may have another cause (no dropped frames between the
  parts); the join still applies because GLOMAP and leftover registration run regardless.
- The CLI's `benchmark --only` now also accepts `a,b` (the documented form failed with
  "unknown benchmark video(s): notre-dame,room").
