# quality_report.json (schema version 1)

Written by the frames stage to `<run>/quality_report.json` on every video run, or alone with
`python -m qarau_pipeline quality --video V --out O` (frames, no reconstruction). Code:
`pipeline/qarau_pipeline/quality.py`. It tells the person who shot the video what to
re-shoot, in plain words. The `code` values are stable: the web app translates them
(RU / KK / EN) and should not show `advice_en` verbatim outside English.

```json
{
 "schema_version": 1,
 "video": "IMG_8315.MOV",
 "duration_s": 47.253,
 "candidate_frames": 424,
 "keyframes": 282,
 "keyframe_selection": {"mode": "parallax", "parallax_frac": 0.0003, "oversample": 1.5,
                        "min_track_frac": 0.6, "rotation_min_gap_s": 0.3, "...": "...",
                        "label": "parallax >= 0.0003 fit to budget, ...",
                        "budget": 283, "parallax_frac_used": 0.00081},
 "thresholds": {"blur_min": 15.0, "blur_rel": 0.35, "...": "..."},
 "verdict": "ok",
 "issues": [
  {"code": "blur", "start_s": 4.95, "end_s": 5.5, "frames": [44, 49],
   "severity": "minor", "metric": "laplacian_var", "value": 10.37825, "threshold": 15.0,
   "advice_en": "Hold the camera steadier and move slower: frames at 5.0–5.5 s are blurred."}
 ]
}
```

| Field | Meaning |
| --- | --- |
| `candidate_frames` | frames decoded (`frames.json` rows); parallax mode: 1.5 × the budget |
| `keyframes` | frames given to COLMAP (`kept: true`), at most `keyframe_selection.budget` |
| `keyframe_selection.parallax_frac_used` | the parallax threshold fitted to the budget |
| `issues[].start_s`, `end_s` | video time (s) of the first and last flagged candidate frame |
| `issues[].frames` | first and last index into `frames.json` |
| `issues[].severity` | `major` if the range lasts ≥ `major_frac` (15 %) of the video, else `minor` |
| `issues[].metric`, `value`, `threshold` | what was measured; `value` is the worst in the range |
| `verdict` | `reshoot_recommended` if any issue is `major`, else `ok` |

Issues shorter than `min_issue_s` (0.5 s) are left out; a single clean frame does not split
a range. Issues are sorted by start time.

## Checks (per candidate frame, 640 px long-side grey image)

| Code | Metric | Flagged when | Advice |
| --- | --- | --- | --- |
| `blur` | `laplacian_var`: variance of the Laplacian (`frames.json` `blur`, image scaled to 640 px wide) | below 0.35 × the video's median (a sudden drop, same rule as the frame drops), or below 15 on a frame with texture | hold steadier, move slower |
| `too_fast` | `flow_frac`: median track displacement over 1/6 s (one candidate step if they are sparser) / diagonal; or `tracks_lost_frac`: share of tracks lost over that step | ≥ 0.15, or ≥ 0.7 lost | move or turn slower |
| `pure_rotation` | `parallax_frac`: median residual after a RANSAC homography over a 1 s window / diagonal | homography inlier ratio ≥ 0.9, parallax < 0.002, while the image moved ≥ 0.03 | walk along or around instead of turning on the spot |
| `low_texture` | `empty_cells_frac`: share of an 8 × 6 grid with no Shi-Tomasi corner (min eigenvalue ≥ 1e-3) | ≥ 0.6 | keep the structure in view, not plain walls/sky/ground |

A frame with a sudden blur drop is `blur`, else a frame with few corners is `low_texture`
(a plain surface has no edges to be sharp), so one frame never gets both.

## Known limits

- A flat surface filmed head-on (a wall, nadir drone over flat ground) moves like a
  homography, so it can be reported as `pure_rotation`.
- Haze and sun glare lower the Laplacian variance like blur does (notre-dame 17–20 s).
- Times are on the candidate grid (9 fps, or slower when the frame cap applies), so a
  range edge can be off by one candidate interval.

## Seen on the reference videos (P2b), checked by eye on contact sheets

- notre-dame: `ok`, one minor `blur` at 17.5–19.7 s (into the sun, hazy).
- room: `ok`, 9 minor issues: plain walls (`low_texture` 21, 25–26, 32–34, 40–43 s),
  motion blur in fast pans (`blur` 5.0–5.5 s, 31.2–32.0 s), a fast pan to the curtain
  (`too_fast` 44.4–45.0 s), short turns in place (`pure_rotation` start, 45 s).
- aegviidu: `reshoot_recommended`: `pure_rotation` 34.2–67.3 s (major) is a panorama
  filmed from the tower top, turning in place over a distant landscape.
- orono: `reshoot_recommended`: from 11 s the drone hovers by the tower top and pans over
  distant land (`pure_rotation`, 44.1–59.1 s major); `too_fast` 1.4–5.0 s is the fast
  sweep past the tank. Both reconstruct, but those stretches add little depth.
