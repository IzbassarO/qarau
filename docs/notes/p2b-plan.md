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

1. **Candidates = the old fixed-rate grid** (same decode, HDR, rotation). Decoding a denser
   grid for capped videos would add bridges and matching time on orono/aegviidu (they were
   near the timeout at 300 frames); not worth it now.
2. **One KLT pass** (`keyframes.track`): 640 px long-side grey, `goodFeaturesToTrack` +
   `calcOpticalFlowPyrLK` frame to frame, track ids, re-detect when tracks thin out. Both
   keyframe selection and the quality checks read these tracks.
3. **Pair geometry** between two frames over their common tracks: median raw flow,
   RANSAC homography, median residual after it (= parallax), inlier ratio.
4. **Keyframes** (`select_keyframes`, mode `parallax`): walk usable (not dropped)
   candidates; new keyframe when parallax since the last keyframe ≥ `parallax_frac` of the
   diagonal, or fewer than `min_track_frac` of the keyframe's tracks survive. If the
   homography explains the motion (pure rotation), the track-loss rule may fire only
   `rotation_min_gap_s` after the last keyframe. First and last usable frame always kept;
   more than the cap → uniform subsample. Non-keyframes: `kept=False`,
   `drop_reason="not_keyframe"`; they are not bridges (bridges stay quality drops).
   Mode `fixed` = old behaviour. Mode + thresholds go to the frames stage metrics and the
   benchmark Notes.
5. **Quality** (`quality.py`): per candidate blur (Laplacian variance at 640 px), too fast
   (flow to the next candidate / diagonal, or track loss), pure rotation (pair over a ~1 s
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
`parallax_frac` 0.005 would keep 40/109 notre-dame frames, 0.01 only 22: far fewer points,
and the gate fails at -10 %. Default 0.0003 keeps notre-dame 109/109, room 264/269,
aegviidu 134/150 (dropped: the still hover at the start/end and a few frames of the pan
from the tower top, 35-55 s, which the report flags as `pure_rotation`). Larger thresholds
are for when matching time matters more than points (`KeyframeParams`).

## Results

Quick benchmark (`23cf1a2+dirty`, keyframes parallax 0.0003 / 0.6 / 0.3 s) vs dev baseline:

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
