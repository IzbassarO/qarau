# P3c — re-gate mykolaiv: plan and diagnosis

Goal (backlog P3c): mykolaiv gives 1 model with ≥ 90 % registered frames, stable across runs,
then remove its `gates.benchmark.per_video` exception. Otherwise document why and keep it
excluded.

**Result: not reachable, and it should not be reached.** `mykolaiv_oil_tanks.webm` is not a
drone survey of tanks. It is an edited news clip (State Emergency Service of Ukraine, Mykolaiv
region) of a night-time fire at an oil depot: ~18 separate shots joined by hard cuts, title
cards at the start and end, and a broadcaster logo burned into the top-left corner of every
frame. Separate shots of different places share no scene geometry, so one honest model of
≥ 90 % of the frames does not exist. The exception stays; the measured reason is in
`docs/notes/benchmark-method.md` → "Mykolaiv (P3c)".

## Plan

1. Run the pipeline CLI (not the benchmark) on the current code (`run --video …`, product
   defaults), keep `colmap/database.db` and every model, and record model sizes, frame ranges,
   where the split is, the frame quality at the split, and what the P3 merge step did.
2. Pick a lever from the task's list (denser sampling, wider matching, mapper options,
   permissive verified merge) that fits the cause; unit-test every new branch.
3. Run the CLI twice with the final settings; record both runs and the spread.
4. Re-gate if both runs give 1 model and ≥ 90 %, else document and keep the exception.
5. Quick benchmark (notre-dame, room) as the regression check; ruff, mypy, pytest.

Step 1 showed that the cause is the input itself (below), so step 2 has no lever to apply:
no pipeline code changed, and step 4 is the "document" branch.

## Diagnosis (run 1, scratch scripts, not in the repo)

Video: 1280×720 VP9, 29.97 fps, 81.6 s → 300 frames at 3.67 fps, 210 kept, 90 dropped (all
for **exposure**, none for blur: night footage: 42–97 % of the pixels below grey level 10).

Shots (histogram cut detector on consecutive frames, checked by eye on a contact sheet):

| Frames | Content | Kept |
| --- | --- | --- |
| 0–18 | title card (static graphic, fades in) | 16 |
| 19–41 | dark tank with flames (mostly too dark) | 2 |
| 42–82 | burning tanks, several angles | 25 |
| 83–152 | fire trucks at night, several shots | 46 |
| 153–178 | wide fire shot (mostly too dark/saturated) | 4 |
| 179–280 | firefighters with hoses and manifolds, ~10 shots | 101 |
| 281–283 | fade to black | 0 |
| 284–299 | title card again | 16 |

Models (mapper output 3 / 178 / 17 images; final after the P3 merge step):

| Model | Images | Frames | Points |
| --- | --- | --- | --- |
| 0 (main) | 178 | 32–280: **every** kept frame of real footage, 16 shots | 13 957 |
| 1 | 17 | 3–297: title card only | 597 |
| unregistered | 15 | 4–7, 16–18, 286–299: title card only | — |

- **Where it splits**: at the title cards. All 32 kept frames outside the main model are
  title-card frames (15 % of the kept frames). They match the footage only through the fade
  frame 283 (title fading in: 105–183 inliers), which the quality check drops as too dark.
  So even a perfect single model of the footage scores 178/210 = **84.8 %**, the number the
  gate saw. ≥ 90 % would need the title card graphic registered into the same 3D scene as the
  fire.
- **The main model is not one scene either.** One model spanning 16 different shots is not
  physically possible. What holds it together is the **logo**: 2 398 of its 3D points are
  seen in more than one shot, and 68 % of their observations lie in the top-left strip of the
  image (x < 160 px, y < 240 px), where the logo sits. COLMAP's own watermark check already
  flags 33 533 of the 44 850 matched pairs as `WATERMARK` (config 7) and ignores them, but
  pairs where the logo matches mix with a few real matches pass as `UNCALIBRATED` /
  `PLANAR_OR_PANORAMIC` and chain the shots. Symptoms: 15 cameras (frames 231–245, one shot)
  sit more than 20 units from the origin, up to ~2 000 (median camera distance 0.68); export's
  reprojection check has a mean of 16.7 px, max 921 px (good videos: < 1 px). The P0 crash (`OverflowError`,
  points ~1e8 away) was the same effect.
- **P3 merge step**: attempted (32 kept frames left over, 2 models). Candidates by kept frames
  registered: `mapped` 178, `global_join` (GLOMAP over all 300 incl. 90 bridges) **43**,
  `leftover_registration` 178. `mapped` kept, so nothing was merged: GLOMAP, which needs
  consistent rotations, cannot chain unrelated shots, and leftover registration has no 3D
  points of the title card to localize against.
- **Run 2** (same code): mapper output 3 / 32 / 19 / 90; leftover registration took the
  90-frame model to 178 (84 bridges used, then dropped). Final partition identical: 178
  footage frames in the main model, all 32 title-card frames in model 1. Numbers and spread:
  `docs/notes/benchmark-method.md` → "Mykolaiv (P3c)".
- **Neighbouring frames at the split**: 18 → 19 is a hard cut (title → night fire); 19–31 are
  dropped as too dark (mean 2–12). 280 → 284 is a fade through black (281–283 dropped).
  Not blur, not a fast pan, not pure rotation: there is no common scene.

## Why none of the task's levers apply

- (a) **Denser sampling / re-extracting frames in the gap**: the gaps are cuts and
  black frames, not sampling holes. More frames of a title card or of the next shot add no
  shared geometry. The dropped dark frames are already offered as bridges by P3 (90 of
  them): GLOMAP got 43, leftover registration 0 more.
- (b) **Wider matching window**: ≤ 300 frames already use exhaustive matching; every pair is
  matched.
- (c) **Looser mapper options**: lower `init_min_tri_angle` / `abs_pose_min_num_inliers`
  would only register more frames through the logo matches: a higher registered % on a
  wronger model.
- (d) **Permissive merge**: the two models share no image and no real correspondence; any
  merge threshold low enough to accept them would accept the logo.

Each of these would raise the number the gate checks by making the map worse. A gate on
mykolaiv would then defend a broken model. The exception stays.

## Regression check (quick benchmark, 0f867c2+dirty, 2026-10-16)

No pipeline code changed (only the mykolaiv `notes` text in `benchmark_set.toml`).

| Video | Registered % | Models | Reproj. px | Points | vs dev baseline | vs 2ccf7f2 | Time s |
| --- | --- | --- | --- | --- | --- | --- | --- |
| notre-dame | 100.0 | 1 | 0.395 | 43 368 | +0.03 % (43 355) | +0.01 % (43 363) | 132.3 |
| room | 100.0 | 1 | 0.94 | 27 300 | −0.2 % (27 350) | +2.3 % (26 676) | 358.6 |

## Follow-ups (not in this task)

- **Owner decision (data):** replace mykolaiv in `benchmark_set.toml` with a continuous drone
  survey of oil tanks (the asset type it was meant to stand for). Needs a video with a
  licence that allows our use; mykolaiv's licence was never recorded.
- **Burned-in overlays** (broadcaster logos, DJI on-screen display, timestamps) are a real
  product risk: they tie unrelated frames together. A static-overlay mask (pixels that stay
  constant across the video → no features there) would fit the per-frame mask planned for the
  sky in P3b (`ImageReaderOptions.mask_path`). On mykolaiv it would *lower* registered %,
  which is the honest result.
- **Shot-cut check** in the frame quality step: an edited video is not one walk-through;
  report the cuts as "re-shoot as one continuous video" instead of building a false map.
- `devteam/config.yaml` → `per_video.mykolaiv.why` still says "gate again after P3"; it
  should point to this note. devteam/ is outside this task's editable paths.
