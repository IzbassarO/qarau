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
 "duration_s": 47.2,
 "candidate_frames": 283,
 "keyframes": 264,
 "keyframe_selection": {"mode": "parallax", "parallax_frac": 0.0003, "min_track_frac": 0.6,
                        "rotation_min_gap_s": 0.3, "...": "...", "label": "parallax 0.0003, ..."},
 "thresholds": {"blur_min": 15.0, "blur_rel": 0.35, "...": "..."},
 "verdict": "ok",
 "issues": [
  {"code": "too_fast", "start_s": 4.75, "end_s": 5.25, "frames": [28, 31],
   "severity": "minor", "metric": "tracks_lost_frac", "value": 0.95238, "threshold": 0.7,
   "advice_en": "Move or turn slower: at 4.8–5.3 s the view changes too much between frames for the map to follow."}
 ]
}
```

| Field | Meaning |
| --- | --- |
| `candidate_frames` | frames decoded on the fixed-rate grid (`frames.json` rows) |
| `keyframes` | frames given to COLMAP (`kept: true`) |
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
| `too_fast` | `flow_frac`: median track displacement to the next candidate / diagonal; or `tracks_lost_frac`: share of tracks lost to the next candidate | ≥ 0.15, or ≥ 0.7 lost | move or turn slower |
| `pure_rotation` | `parallax_frac`: median residual after a RANSAC homography over a 1 s window / diagonal | homography inlier ratio ≥ 0.9, parallax < 0.002, while the image moved ≥ 0.03 | walk along or around instead of turning on the spot |
| `low_texture` | `empty_cells_frac`: share of an 8 × 6 grid with no Shi-Tomasi corner (min eigenvalue ≥ 1e-3) | ≥ 0.6 | keep the structure in view, not plain walls/sky/ground |

A frame with a sudden blur drop is `blur`, else a frame with few corners is `low_texture`
(a plain surface has no edges to be sharp), so one frame never gets both.

## Known limits

- A flat surface filmed head-on (a wall, nadir drone over flat ground) moves like a
  homography, so it can be reported as `pure_rotation`.
- Haze and sun glare lower the Laplacian variance like blur does (notre-dame 17–20 s).
- Times are on the candidate grid (6 fps, or slower when the frame cap applies), so a
  range edge can be off by one candidate interval.

## Seen on the reference videos (P2b)

- notre-dame: `ok`, one minor `blur` at 17.4–19.8 s (into the sun, hazy).
- room: `ok`, 11 minor issues, all checked by eye: plain walls (`low_texture`), fast
  pans with motion blur (`too_fast`, `blur`), short turns in place at the start and at 45 s
  (`pure_rotation`).
