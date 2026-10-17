# P2b plan: parallax keyframes + footage quality report

## Today (read before the change)

- `frames.run`: ffmpeg decodes `clamp(duration·6, 60, 300)` frames (or `--max-frames`) by
  pts, HDR→SDR, autorotate once; OpenCV scores blur/exposure; `decide_drops` marks up to 30 %
  as `blur`/`exposure` (kept=False). Every kept frame goes to COLMAP; dropped ones are
  bridges for the P3 merge. Exhaustive matching ≤ 300 frames.
- Benchmark caps: aegviidu/orono `max_frames = 150`.
- The merge gate fails a video on > 2 pp registered drop, more models, > 10 % reprojection
  rise **or > 10 % fewer points**. Fewer frames means fewer points, so the parallax mode
  must not thin moving footage; it removes only frames that add no geometry.

## Design

1. **Budget and candidates.** The old frame count (`clamp(duration·6, 60, 300)` or
   `--max-frames`) stays the budget COLMAP gets. Parallax mode decodes 1.5 × that many
   candidates (same decode path, HDR, rotation; at most the source fps). Merge bridges stay
   quality drops, capped at 30 % of the keyframes, so the denser grid adds no merge
   matching time on orono/aegviidu (they were near the timeout at 300 frames).
2. **One KLT pass** (`keyframes.track`): 640 px long-side grey, `goodFeaturesToTrack` +
   `calcOpticalFlowPyrLK` frame to frame, track ids, re-detect when tracks thin out. Both
   keyframe selection and the quality checks read these tracks.
3. **Pair geometry** between two frames over their common tracks: median raw flow,
   RANSAC homography, median residual after it (= parallax), inlier ratio.
4. **Keyframes** (`keyframes.fit`, mode `parallax`): walk usable (not dropped)
   candidates; new keyframe when parallax since the last keyframe ≥ θ · diagonal, or fewer
   than `min_track_frac` of the keyframe's tracks survive. If the homography explains the
   motion (pure rotation), the track-loss rule may fire only `rotation_min_gap_s` after the
   last keyframe. θ starts at `parallax_frac` (0.0003, the floor above a still camera) and
   is raised by log bisection until the keyframes fit the budget: the same number of frames
   as before, placed by camera motion instead of the clock. Track-loss keyframes alone over
   the budget → uniform subsample. First and last usable frame always kept. Non-keyframes: `kept=False`,
   `drop_reason="not_keyframe"`; they are not bridges (bridges stay quality drops).
   Mode `fixed` = old behaviour. Mode + thresholds go to the frames stage metrics and the
   benchmark Notes.
5. **Quality** (`quality.py`): per candidate blur (Laplacian variance at 640 px), too fast
   (flow over 1/6 s / diagonal, or track loss, whatever the candidate density), pure rotation (pair over a ~1 s
   window: homography explains it, motion but no parallax), low texture (Shi-Tomasi
   min-eigenvalue per grid cell, fraction of empty cells). Consecutive flagged frames →
   time ranges → `quality_report.json` (format: `docs/notes/quality-report.md`).
6. CLI: `run --keyframes parallax|fixed`; `quality --video V --out O` = frames stage only.

## Known limit

A flat surface filmed head-on (wall, nadir drone over flat ground) moves like a homography,
so it looks like pure rotation: fewer keyframes there (track loss still keeps overlap) and
the report may say "turning on the spot". Noted in the report doc.

## Thresholds: what the real footage said

Parallax per 1/6 s at 640 px is tiny on our videos (median residual / diagonal: notre-dame
0.0018, room 0.0011, aegviidu 0.0004 at its 2 fps cap); a still camera measures ~5e-5.
A fixed `parallax_frac` cannot fit all videos: 0.005 keeps 40/109 notre-dame frames (far
fewer points; the gate fails at -10 %), while 0.0003 keeps 109/109, i.e. it changes
nothing (round 1, sent back by the tester for that). Hence the budget fit (round 2).

### Round 2: denser candidates, threshold fitted to the budget

Room at 2 × candidates (quick benchmark, 283 frames): 100 %, 1 model, but points 24 317
(−11 % vs dev 27 349; room's recorded runs spread 26 482–27 349) and reproj 0.992. Without
the rotation gap: 24 966, so not that. At 1.5 ×: 26 632, then 26 729 in the benchmark
below. With 2 × the frames bunch into the fast parts; 1.5 × is the default.

1.5 × candidates, keyframe gaps in seconds (notre-dame/room from the benchmark below,
aegviidu/orono frames stage only via the `quality` command with their `max_frames` 150;
since round 3 those two run the fixed grid, see below):

| Video | Candidates | Keyframes (budget) | θ fitted | Gap median / p90 / max | Fixed grid gap |
| --- | --- | --- | --- | --- | --- |
| notre-dame | 182 | 121 (121) | 0.0012 | 0.13 / 0.23 / 1.77 | 0.17 |
| room | 424 | 282 (283) | 0.00081 | 0.12 / 0.23 / 0.88 | 0.17 |
| aegviidu (cap 150) | 225 | 150 (150) | 0.00038 | 0.33 / 0.93 / 4.44 | 0.47 |
| orono (cap 150) | 225 | 150 (150) | 0.00032 | 0.40 / 0.83 / 1.63 | 0.60 |

Same frame count as the fixed grid, different frames: the fitted θ is 1.1–4 × the floor;
gaps shrink to 0.12–0.13 s where the camera moves and grow to ~0.9 s in turns and pans
(aegviidu's tower-top panorama) and 4.4 s in its final hover. Track-loss keyframes keep
≥ 60 % shared tracks between neighbours, so the map stays connected. The notre-dame 1.8 s
gap is the blurred stretch (17.5–19.7 s) the quality drops remove in both modes.

## Results

Round 2 quick benchmark (`9fb3c5f+dirty`, parallax fit to budget, 1.5 × candidates):

| Video | Frames used | Registered % | Models | Points | Reproj. px | Time s |
| --- | --- | --- | --- | --- | --- | --- |
| notre-dame (dev) | 109 | 100.0 | 1 | 43426 | 0.40 | 159.9 |
| notre-dame (P2b) | 121 | 100.0 | 1 | 49895 (+14.9 %) | 0.394 | 189.2 |
| room (dev) | 269 | 100.0 | 1 | 27349 | 0.94 | 442.9 |
| room (P2b) | 282 | 100.0 | 1 | 26729 (−2.3 %) | 0.98 | 432.2 |

More frames used than the dev rows: the blur drops now come out of 1.5 × candidates, so
the full budget survives them (notre-dame 121 instead of 109).

### Round 3: merge gate on round 2

`orono: reprojection 0.57 -> 0.67 px` (+17 %, gate +10 %). Orono's budget is 150 frames
over 91 s (1.65 fps, capped by `max_frames`); the fit moved frames out of the slow
pan/hover parts into the fast ones, and with frames this sparse the slow parts lost
accuracy. The redistribution only pays where the budget has surplus density. So: a budget
capped below 6 fps (long video or `--max-frames`) runs as `fixed`, the exact dev path for
orono and aegviidu (verified: orono 150 frames at 1.6534 fps, all kept, mode
`... budget capped below 6 fps -> fixed`). The round-2 bridge cap (30 % of the keyframes)
now applies to parallax runs only, so it cannot cut orono's bridges below dev's. (A cap of
30 % of keyframes + bridges for both was tried: more bridges on room, points 25 406 /
reproj 0.99 in one run, so the parallax cap stayed as measured in round 2.) Parallax stays on for videos whose budget is the full
6 fps: notre-dame, room, every clip under 50 s.

Round 3 quick benchmark (`4d121b5+dirty`):

| Video | Frames used | Registered % | Models | Points | Reproj. px | Time s |
| --- | --- | --- | --- | --- | --- | --- |
| notre-dame (dev) | 109 | 100.0 | 1 | 43426 | 0.40 | 159.9 |
| notre-dame (P2b) | 121 | 100.0 | 1 | 50064 (+15.3 %) | 0.395 | 174.7 |
| room (dev) | 269 | 100.0 | 1 | 27349 | 0.94 | 442.9 |
| room (P2b) | 282 | 100.0 | 1 | 26397 (−3.5 %) | 0.982 | 357.1 |

### Round 4: report format, max time gap, tests in test_frames.py

- `quality_report.json` version 2: `summary` {`frames_total`, `keyframes`, `issues_count`,
  `verdict`}; issues `kind` (was `code`), `severity` `info`/`warn` (was `minor`/`major`),
  `advice` + `advice_key` (was `advice_en`), `frames` = every flagged frame index.
  `run` and `quality` print `quality.text(report)`.
- Keyframes: `max_gap_s` 2 s adds a keyframe after a long gap if the image moved ≥ 1 % of
  the diagonal (a still camera still gives first + last only). ADR 0015.
- Synthetic keyframe/quality tests moved into `tests/test_frames.py`.

Quick benchmark, before (round-3 code, `d02a466`) → after (round 4, `d02a466+dirty`):

| Video | Frames used | Registered % | Models | Points | Reproj. px | Time s |
| --- | --- | --- | --- | --- | --- | --- |
| notre-dame (before) | 121 | 100.0 | 1 | 49876 | 0.394 | 146.9 |
| notre-dame (after) | 121 | 100.0 | 1 | 49927 | 0.395 | 149.1 |
| room (before) | 282 | 100.0 | 1 | 25278 | 0.99 | 349.7 |
| room (after) | 282 | 100.0 | 1 | 26706 | 0.979 | 308.9 |

Same fitted θ (0.0012 / 0.00081) and identical keyframe sets before and after (the gap rule
did not fire on either video), so the point difference on room is COLMAP run-to-run spread. Reports unchanged in content: notre-dame `ok`, 1
`info` blur (17.5–19.7 s); room `ok`, 9 `info` issues, blur only 5.0–5.5 and 31.2–32.0 s.

Round 1 quick benchmark (`23cf1a2+dirty`, fixed θ 0.0003, old grid) vs dev baseline:

| Video | Frames used | Registered % | Models | Points | Reproj. px | Time s |
| --- | --- | --- | --- | --- | --- | --- |
| notre-dame (dev) | 109 | 100.0 | 1 | 43426 | 0.40 | 159.9 |
| notre-dame (P2b) | 109 | 100.0 | 1 | 43353 | 0.395 | 147.2 |
| room (dev) | 269 | 100.0 | 1 | 27349 | 0.94 | 442.9 |
| room (P2b) | 264 | 100.0 | 1 | 26579 | 0.947 | 400.1 |

Quality reports, checked against contact sheets of every flagged range: notre-dame `ok`
(minor blur 17.4-19.8 s, into the sun); room `ok`, 11 minor issues, all real (plain walls,
fast pans with motion blur, short turns in place).

## Verify

Synthetic tests (static, two-depth translation, K·R·K⁻¹ rotation, cap; one per issue code,
clean footage, report shape). Real: room + notre-dame reports read by eye; quick benchmark
against the dev baseline (100 %/1 model both, points within 10 %).
