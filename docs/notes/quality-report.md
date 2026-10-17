# quality_report.json (version 2)

Written by the frames stage to `<run>/quality_report.json` (next to `frames.json` and
`frames/`) on every video run, or alone with
`python -m qarau_pipeline quality --video V --out O` (frames, no reconstruction). Both
commands print a short summary (verdict, counts, one line of advice per issue). Code:
`pipeline/qarau_pipeline/quality.py`. It tells the person who shot the video what to
re-shoot, in plain words. `kind` and `advice_key` are stable: the web app translates
`advice_key` (RU / KK / EN, the time range from `start_s`/`end_s`) and should not show the
English `advice` verbatim outside English.

```json
{
 "version": 2,
 "video": "IMG_8315.MOV",
 "duration_s": 47.253,
 "summary": {"frames_total": 424, "keyframes": 282, "issues_count": 1, "verdict": "ok"},
 "keyframe_selection": {"mode": "parallax", "parallax_frac": 0.0003, "oversample": 1.5,
                        "min_track_frac": 0.6, "max_gap_s": 2.0, "...": "...",
                        "label": "parallax >= 0.0003 fit to budget, ...",
                        "budget": 283, "parallax_frac_used": 0.00081},
 "thresholds": {"blur_min": 15.0, "blur_rel": 0.35, "...": "..."},
 "issues": [
  {"kind": "blur", "start_s": 4.95, "end_s": 5.5, "frames": [44, 45, 46, 48, 49],
   "severity": "info", "metric": "laplacian_var", "value": 10.37825, "threshold": 15.0,
   "advice": "Hold the camera steadier and move slower: frames at 5.0–5.5 s are blurred.",
   "advice_key": "quality.advice.blur"}
 ]
}
```

| Field | Meaning |
| --- | --- |
| `version` | format version; 2 since P2b round 4 (1 had `schema_version`, `code`, `advice_en`, `minor`/`major`) |
| `summary.frames_total` | candidate frames decoded (`frames.json` rows); parallax mode: 1.5 × the budget |
| `summary.keyframes` | frames given to COLMAP (`kept: true`), at most `keyframe_selection.budget` |
| `summary.issues_count` | `len(issues)` |
| `summary.verdict` | `reshoot_recommended` if any issue is `warn`, else `ok` |
| `keyframe_selection.parallax_frac_used` | the parallax threshold fitted to the budget |
| `issues[].kind` | `blur`, `too_fast`, `pure_rotation`, `low_texture` |
| `issues[].start_s`, `end_s` | video time (s) of the first and last flagged candidate frame |
| `issues[].frames` | the flagged frames: indices into `frames.json` (`i`) |
| `issues[].severity` | `warn` if the range lasts ≥ `major_frac` (15 %) of the video (re-shoot), else `info` |
| `issues[].metric`, `value`, `threshold` | what was measured; `value` is the worst in the range |
| `issues[].advice`, `advice_key` | one plain-English re-shoot sentence naming the time range; `quality.advice.<kind>` for translation |

Issues shorter than `min_issue_s` (0.5 s) are left out; a single clean frame does not split
a range. Issues are sorted by start time.

## Checks (per candidate frame, 640 px long-side grey image)

| Kind | Metric | Flagged when | Advice |
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

- notre-dame: `ok`, one `info` `blur` at 17.5–19.7 s (into the sun, hazy).
- room: `ok`, 9 `info` issues: plain walls (`low_texture` 21, 25–26, 32–34, 40–43 s),
  motion blur in fast pans (`blur` 5.0–5.5 s, 31.2–32.0 s), a fast pan to the curtain
  (`too_fast` 44.4–45.0 s), short turns in place (`pure_rotation` start, 45 s).
- aegviidu (`max_frames` 150, 2.1 fps fixed grid): `reshoot_recommended`: `pure_rotation`
  34.3–66.4 s (`warn`) is a panorama filmed from the tower top, turning in place over a
  distant landscape; two short `too_fast` turns at 57.5 and 65.0 s.
- orono (`max_frames` 150, 1.65 fps fixed grid): `ok`; `too_fast` 1.5–5.1 s is the fast
  sweep past the tank, `pure_rotation` 11.2–22.1 s and two shorter ones later: the drone
  hovers by the tower top and pans over distant land. On the denser parallax grid the
  same video gave a `warn` `pure_rotation` 44.1–59.1 s: on a 0.6 s grid the 1 s rotation
  window shrinks to one 0.6 s step, less motion to judge, so a capped video reports turns
  less readily.
