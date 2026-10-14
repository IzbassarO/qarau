# M1 results — pipeline (Part B)

Machine: Apple M1 Pro, 16 GB, CPU-only pycolmap 4.2.1 (SIFT on CPU). Numbers are from `run.json`
of each run in `data/runs/<name>/`. "Check px" = reprojection of track points with the
**stored aligned pose + camera params read back from the bundle** against COLMAP's own 2D
observations (`reproj_check_px`; independent numpy projection incl. SIMPLE_RADIAL).

## South Building (image set, export only)

128 images, 61 514 points, 128/128 registered, mean track 5.31, COLMAP mean reproj 0.497 px.
Bundle check px: mean 0.52, max 3.84 (15 938 observations). Export 6 s.
z_range [-0.73, 1.09] (COLMAP units are arbitrary, no metric scale); all cameras lie below the cloud
median z, as expected for a walkdown, so up-vector estimation is plausible.

## Videos

| run | video | frames extracted / dropped / sent to COLMAP | registered | points | mean reproj (COLMAP / bundle check) | frames | features | matching | mapping | export |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| notre-dame | 20 s 640x480 | 60 / 6 / 54 | 52 | 15 239 | 0.37 / 0.41 px | 0.4 s | 5.0 s | 5.7 s | 10.2 s | 0.3 s |
| aegviidu (attempt 2) | 71 s 1080p | 177 / 0 / 177 | 127 (5 models) | 47 397 | 0.49 / 0.56 px | 4.9 s | 74 s | 76 s | 222 s | 2.2 s |
| orono | 91 s 1440p | 226 / 0 / 226 | 226 (2 models) | 101 189 | 0.54 / 0.62 px | 14 s | 214 s | 97 s | 369 s | 5.0 s |

Max check error is 3.8-4.0 px on every run (a few outlier tracks); means match COLMAP's own error.

## What failed and what I tried

**aegviidu, attempt 1** (rule from the plan: clipped > 40 % -> drop): 177 frames, 53 dropped
(30 % cap hit), all for `exposure` in one contiguous block (frames 72-124). Cause: sky is
>245 on 41-47 % of pixels in that part of the orbit, so the rule marked good frames bad.
COLMAP got 124 frames with a 53-frame hole: 72 registered, 7 separate models, 39 463 points,
374 s. Fix: `clip_high` threshold 0.4 -> 0.7 (low-clip stays 0.4; mean<20 / >235 unchanged).
Attempt 2 (table above): no drops, 127 registered but still 5 models (orbit breaks: one model of
127 of 177 frames). Not tuned further (no more features / lower peak threshold tried); the
remaining 50 frames are in smaller fragments, likely low-texture sky/tower segments.
Merging fragments or loop detection is the next lever.

**orono**: no failure; 2 models but the largest holds all 226 frames (the second is a tiny stray).
Not run with the old rule, so no attempt 1.

**mykolaiv_oil_tanks**: not run (optional, lowest priority).

## Known limits

- Coordinates are COLMAP-arbitrary scale (no metric scale until GPS / reference object).
- Sparse clouds only (15k-100k points); dense needs CUDA (ADR 0004).
- Image-set `source.duration_s`/`fps` are synthetic (`n-1`, 1.0) since t = index.

## Viewer (Part C), verified 2026-10-14

Playwright (Chromium, `--use-angle=metal`, real GPU) through the real API (`make api`), all runs:

| run | points | load | fps while orbiting |
| --- | --- | --- | --- |
| south-building | 61 514 | 0.4 s | 60 |
| notre-dame | 15 239 | 0.3 s | 60 |
| aegviidu | 47 397 | 0.7 s | 60 |
| orono | 101 189 | 0.5 s | 60 |
| synthetic-5m | 5 000 000 | 4.9 s | 60 |

60 fps is the vsync cap. The 5 M bundle is 644 MB, mostly the per-frame visibility index of a
worst-case synthetic scene; real sparse runs are 1–5 MB. Headless SwiftShader (CI default) is
software rendering and only good for screenshots.

Screenshots: `docs/notes/m1/` (built from the Playwright run). View through the video camera
lines up with the real extracted frame on every run (horizon, facade windows, field edges).

## Acceptance vs `docs/tasks/M1.md`

The task asks for three phone videos shot by us. The owner has no footage yet and asked for
public data only, so the three test videos are public drone videos (below). Phone footage of a
pipe, a railing and a building corner is still needed before M1 can be called done on our own
data.

## Test data and licences

Nothing here is committed; all lives in `data/samples/` (gitignored).

| file | source | licence |
| --- | --- | --- |
| south-building | COLMAP sample dataset, github.com/colmap/colmap/releases/download/3.11.1/south-building.zip | COLMAP sample data |
| notre_dame_oubiels.webm | Wikimedia Commons, "Notre Dame des Oubiels drone.webm", by Lothur | CC0 |
| aegviidu_water_tower.ogv | Wikimedia Commons, "Aegviidu veetorn (Aegviidu water tower).ogv", by Sillerkiil | CC0 |
| orono_water_tower.webm | Wikimedia Commons, "Orono Leaning Water Tower Drone Video (2022-07-10).webm", by Drozwik Drones | CC BY 3.0 |

Screenshots in `docs/notes/m1/` that show the Orono frame are derived from Drozwik Drones'
video (CC BY 3.0).
