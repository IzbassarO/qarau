# Dense speed: COLMAP PatchMatch knobs (P4a-3)

Goal (owner, 2026-10-21): a 3-min 1080p video, which the pipeline caps at 300 frames, goes
through the remote dense job in **under 30 min** (start, upload, PatchMatch, fusion, download).
The knobs are `dense_remote.patch_match` in `pipeline/config.yaml`; every key is optional and
an absent one keeps COLMAP's default and the pre-P4a-3 commands exactly.

## Baseline (measured, P4a-2a-run, 2026-10-21)

notre-dame-p3e, **121 views at 640x480 on an L4**, COLMAP 4.2.1 defaults:

| Step | Time | Per view |
| --- | --- | --- |
| PatchMatch photometric | 11.6 min | **5.8 s** |
| PatchMatch geometric | 15.0 min | 7.4 s |
| stereo_fusion | 12 s | 0.1 s |
| whole job | 1681 s of timeout_s 1800 | |

"About 5.8 s per view per pass" is the photometric pass; the geometric pass is slower (it also
reads the source depth maps), so the model below uses both measured passes: **13.2 s per view**.

At that pace orono-p3e (151 frames at 1920x1080) needs about 3.7 h with the defaults (table
below), so it was not run.

## Scaling model

PatchMatch time per view ~ pixels x iterations x sources x window samples.

- pixels: COLMAP processes each view at its size after `max_image_size` (longest side);
  1920x1080 at 960 is 960x540.
- iterations: `num_iterations`, default 5. Each iteration is a full sweep over the image.
- sources: frames compared against each view (`__auto__, N` in `stereo/patch-match.cfg`,
  default 20). The cost of a hypothesis is computed against every source.
- window samples: the NCC window is sampled every `window_step` pixels within
  `window_radius`: (2r/step + 1)^2 samples. Default radius 5, step 1: 121; radius 5, step 2: 36
  (0.30x, a bit more than the plain 1/step^2).

Assumptions: linear in each factor; the same GPU (L4); no per-view fixed cost (reading images,
GPU setup), which is why the estimate below doubles the PatchMatch time as a margin. Fusion
scales with pixels x views (12 s x 1.69 x 300/121 = 50 s at 960 px).

## Per-view time (both passes) and 300 frames

| Setting | Factor | s per view | 300 frames | orono (151) |
| --- | --- | --- | --- | --- |
| 640x480, defaults (measured) | 1.00 | 13.2 | 66 min | |
| 1920x1080, defaults | 6.75 | 89.0 | 7.4 h | 3.7 h |
| 960 px | 1.69 | 22.3 | 111 min | 56 min |
| 960 px, 3 iterations | 1.01 | 13.4 | 67 min | 34 min |
| 960 px, 3 iterations, 8 sources | 0.41 | 5.3 | 27 min | 13 min |
| **960 px, 3 iterations, 8 sources, step 2 (proposed)** | **0.12** | **1.6** | **7.9 min** | **4.0 min** |
| same, window_radius 4 | 0.08 | 1.1 | 5.5 min | 2.8 min |
| 1280 px, 3 iterations, 8 sources, step 2 | 0.21 | 2.8 | 14 min | 7.1 min |
| 1920 px, 3 iterations, 8 sources, step 2 | 0.48 | 6.4 | 32 min | 16 min |

## Proposed values for a 3-min 1080p video (300 frames)

```yaml
dense_remote:
  patch_match:
    max_image_size: 960
    num_iterations: 3
    window_step: 2
    num_sources: 8
    # window_radius: not set (5), so the window still covers the same 11x11 patch
```

Estimated job: PatchMatch 7.9 min, doubled as the margin = 16 min; fusion about 1 min; start,
upload, undistortion and download about 5 min (an assumption: the measured run spent
1681 - 1596 - 12 = 73 s on them for 121 small frames; 300 1080p frames are ~10x the bytes).
**About 22 min, under the 30 min target** and inside today's `timeout_s: 1800`. Time queued
for a GPU (28 min once on 2026-10-21) is not in this estimate, but it does count against
`timeout_s`: the worst case, L4 then L40S each waiting the full `start_timeout_s: 300`, adds
10 min (32 min > 1800 s). If that happens, the owner can raise `timeout_s` (with
`est_hours_per_job` covering it); nothing here changes either value.

- **The geometric pass is kept.** `stereo_fusion --input_type geometric` reads its output, and
  it is what removes the floating noise of photometric-only depth maps. Dropping it would save
  roughly half of PatchMatch, but it is not a knob in this task; it is the next lever only if
  the measured time misses the target.
- 960 px depth maps are still 0.5 Mpx per view; with 300 views the fused cloud stays dense
  for the viewer. If the owner finds it too coarse, 1280 px with the same knobs is about
  14 min of PatchMatch (28 min with the margin): tight against 30 min.
- These are the levers COLMAP's FAQ ("Speedup dense reconstruction") names: a smaller
  max_image_size, fewer iterations, a larger window step, fewer source images in
  patch-match.cfg. Their effect on our clouds is what the timing run must show.
- The values are left commented in `config.yaml` until the timing run confirms them.

## How the knobs reach COLMAP

| Key | Range | COLMAP |
| --- | --- | --- |
| max_image_size | >= 1 | `image_undistorter --max_image_size N` and `patch_match_stereo --PatchMatchStereo.max_image_size N` |
| num_iterations | >= 1 | `--PatchMatchStereo.num_iterations N` |
| window_radius | 1..32 | `--PatchMatchStereo.window_radius N` |
| window_step | 1..2 | `--PatchMatchStereo.window_step N` |
| num_sources | >= 1 | a step between undistort and PatchMatch rewrites every `__auto__, 20` line of `stereo/patch-match.cfg` to `__auto__, N`, then fails if no line says `__auto__, N` |

Ranges are COLMAP 4.2.1's `PatchMatchOptions::Check` (checked with pycolmap 4.2.1: radius 33
and step 3 fail). The cfg format was checked by running pycolmap 4.2.1's undistorter: two lines
per image, the name then `__auto__, 20`. An invalid or unknown key fails config loading with
an error naming `dense_remote.patch_match.<key>`. `dense-remote --provider fake` prints every
command with its flags (a free dry run).

## Validation (owner, after this task merges)

The benchmark has no 3-min 1080p video. The owner measures on Modal (paid, like P4a-2a-run)
with the proposed values uncommented: **aegviidu** (1920x1080, 71 s) and **orono**, and scales
both to 300 frames. Then the values are set in `config.yaml` (or tuned with the table above:
the per-view time of a run divided by the factor of its row gives the real constant).
