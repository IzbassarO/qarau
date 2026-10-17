# Architecture

The short version lives in `CLAUDE.md`. This document explains how the pieces work
together and why.

## Data flow

```
Capture (video + telemetry)
   │  upload / import
   ▼
Object storage ── Postgres (metadata, findings, decisions)
   │  job enqueued
   ▼
Pipeline worker (GPU)
   frames → quality → reconstruction → segmentation → projection → measurement
          → [registration → change detection] → coverage → risk ranking → report
   │  writes artifacts + rows
   ▼
API (FastAPI) ──► Web viewer / Desktop app / PDF & XLSX
                         │
                         ▼
                 Engineer confirms or rejects → Decision log (append-only)
```

The viewer only reads finished artifacts (Potree octree, findings JSON), so the map opens
instantly; it never waits on processing.

## Stage contracts

Each stage reads inputs from storage by key and writes outputs under
`inspections/<inspection_id>/<stage>/`. A stage records `status`, `started_at`,
`finished_at`, `tool_version`, `params` and `metrics` in the `stage_runs` table. Re-running
a stage overwrites its own folder only. This makes every result reproducible and every
failure local.

| Stage | Inputs | Outputs |
| --- | --- | --- |
| frames | video (or image set) | `probe.probe_video()` → `VideoInfo` (codec, coded and display size, `rotation_deg` from the displaymatrix or `tags.rotate`, transfer/primaries/bit depth, `is_hdr`, fps, `is_vfr` from the pts of the first 120 packets: any interval > 25 % off the median; `frame_dt_s` = that median interval). Then one ffmpeg decode: autorotate (once, ffmpeg default; first frame checked against the display size), HDR (HLG/PQ) → 8-bit SDR BT.709 (`zscale`+`tonemap=hable` when the build has zscale, else `colorspace` with a warning), frames picked by presentation time with `select` on the same grid as the former `fps` filter (per tick k the last source frame before (k+½)/rate, taken as the first frame at or after that boundary minus the median frame interval; no duplicates, `-fps_mode passthrough`; rate `clamp(duration·6, 60, 300)` frames). Outputs: `frames/000000.jpg…` (long side ≤ 1920 px), `frames/manifest.json` (`source` VideoInfo, `conversions` e.g. `autorotate:90`, `tonemap:hable`/`tonemap:colorspace`, `vfr:pts_sampling`; `frames[{file, pts_s}]` = source pts; `warnings`), `frames.json` (`source`; per frame `i`, `file`, `t`, `blur`, `exposure{mean,clip_low,clip_high}`, `kept`, `drop_reason`; GPS later). The frame count above is the **budget** COLMAP gets. With **parallax keyframes** (`keyframes.py`, ADR 0015, `run --keyframes parallax`, default) ffmpeg decodes `oversample` (1.5) × the budget as **candidates** (at most the source fps), and after the blur/exposure drops the keyframes are picked by camera motion. KLT tracks (`goodFeaturesToTrack` + pyramidal LK, 640 px long side) run over all candidates; a new keyframe starts when the median residual after the best RANSAC homography since the last keyframe reaches the parallax threshold (fraction of the diagonal), or fewer than `min_track_frac` (0.6) of its tracks survive, or `max_gap_s` (2 s) has passed while the image moved ≥ `min_motion_frac` (1 % of the diagonal; a still camera's near-duplicates never become keyframes); when the homography explains the motion (turning on the spot) track loss adds keyframes at most every `rotation_min_gap_s` (0.3 s). The threshold starts at `parallax_frac` (0.0003, above a still camera's ~5e-5) and is raised by bisection until the keyframes fit the budget, so the budget goes where the camera moves (still and turn-in-place stretches get few frames, fast parts more); if track-loss keyframes alone exceed it → uniform subsample. First and last usable frame always kept. Other candidates get `kept=false`, `drop_reason="not_keyframe"` and are not merge bridges (bridges = quality drops; with parallax at most 30 % of the keyframes, fixed grid unchanged). When the budget is capped below 6 fps (long videos, `--max-frames`, e.g. the 150-frame drone benchmarks) parallax runs as `fixed`: the frames are already sparse and moving them out of the slow parts raised orono's reprojection 0.57 → 0.67 px; `keyframe_mode` says so (`budget capped below 6 fps -> fixed`). `--keyframes fixed` decodes the budget and keeps every usable frame (pre-P2b). Mode, thresholds and the fitted threshold are in the stage metrics (`keyframe_mode`, `keyframe_selection.parallax_frac_used`) and the benchmark Notes. Unprobeable file, decode failure or 0 frames → `StageError` with a readable message |
| quality | candidates + KLT tracks (inside the frames stage) | `<run>/quality_report.json`, next to `frames.json` (version 2, format: `docs/notes/quality-report.md`): `version`; `summary` {`frames_total`, `keyframes`, `issues_count`, `verdict` `ok` / `reshoot_recommended`}; `issues[]` {`kind` `blur` (variance of the Laplacian) / `too_fast` (flow or track loss over 1/6 s) / `pure_rotation` (homography explains a 1 s window, no residual parallax) / `low_texture` (grid cells without a corner), `start_s`, `end_s`, `frames` (flagged `frames.json` indices), `severity` `info` / `warn` (≥ 15 % of the video), `metric`, `value`, `threshold`, `advice` (plain-English re-shoot sentence), `advice_key` (`quality.advice.<kind>`, for the web's translations)}. `run` and `python -m qarau_pipeline quality --video V --out O` (frames stage only, no reconstruction) print a short summary |
| reconstruction | kept frames | `colmap/database.db`, `colmap/sparse/0/*.bin` (main model; pycolmap in-process, `Reconstructor` interface; exhaustive matching ≤ 300 frames, else sequential with overlap 20 + quadratic overlap, no vocabulary tree; mapper `incremental` (default) or `global` (GLOMAP)). If the video splits into several models, it is joined again (ADR 0011): the frames the quality check dropped are added as temporary bridges, one GLOMAP pass maps everything, relaxed leftover registration follows if kept frames are still missing, the candidate with the most kept frames wins, bridges are deregistered, global BA. Models that still stand apart are written to `colmap/sparse/1..` and counted, never dropped silently; metrics: registered images, models, mapped model sizes, separate models, merge attempts/candidates, points, mean track length, mean reprojection error, seconds per step |
| export | `colmap/sparse/0`, `frames.json` | `cloud.laz` (xyz+rgb), `cloud.ply`, `web/` viewer bundle: `manifest.json` + raw typed arrays + `thumbs/` (format: ADR 0003, `docs/notes/m1-plan.md` §3); world is Z-up, centroid at origin, `world_from_colmap` stored. Before writing, points with a non-finite coordinate or farther from the per-axis median than max(100 × the 99th-percentile distance, 1) are dropped, counted in `manifest.stats.dropped_points` and printed. LAS offset = middle of the kept extent, scale = max(1 mm, half extent / (2³¹−1) × 1.1), so quantized coordinates always fit in int32 |
| segmentation | bundle frames, text prompts, a `Segmenter` | `segmentation/observations.json` + `segmentation/masks/<frame>_<k>.png` (prompt, confidence, bbox per instance); implementations: manual masks, Grounded SAM 2 (dev only), SAM 3.1 API stub (ADR 0009) |
| projection | masks, poses, tracks | `findings/findings.json` + `findings/points.finding.u16`, manifest `findings` key; visibility from tracks, voting, clustering (ADR 0009) |
| measurement | clustered points, scale | area is part of the findings file: m² only with a known scale, else `rel` (ADR 0009) |
| registration (monitoring) | new frames, baseline model | transform to baseline, aligned cloud |
| change detection | findings now vs baseline/previous | `changes.json` (new / grew / shrank / stable) |
| coverage | poses, usable frames, cloud | per-point/voxel state: inspected_ok / finding / not_seen |
| risk ranking | findings, changes, asset data | severity + reason text per finding |
| report | everything above | `report.pdf`, `report.xlsx`, work-order drafts |

## Key techniques

**Masks → 3D points.** For frame *i* we know camera intrinsics *K* and pose *[R|t]* from
reconstruction. Project every visible 3D point into frame *i*; points whose projection
falls inside a defect mask (and passes a depth/visibility check) get that finding's label.
A finding is confirmed in 3D when it is seen in ≥ 2 frames from different angles; single-
frame findings are kept but marked "low evidence".

**Scale.** Phone video has no scale. Use drone GPS when present; otherwise a reference
object of known size or a manual two-point measurement in the viewer. Areas are never
shown in m² without a scale source; the UI says "relative size" instead.

**Coverage.** Voxelize the cloud. A voxel is `inspected` if it is seen in at least one
usable frame within a distance and viewing-angle threshold that makes defects of the
target size visible. Everything else is `not_seen`.

**Registration (monitoring).** Localize new frames against the baseline COLMAP model
(feature matching + PnP), reconstruct the new partial cloud in the baseline frame, then
refine with ICP. Reject registrations with low inlier counts and show them as "could not
align" rather than guessing.

**Change detection.** Match findings by 3D proximity and class. Report new, grew (area
increase above noise threshold), shrank (usually a measurement artefact — flag it), and
stable. Noise thresholds are calibrated per asset type from repeated scans.

## Benchmark

`python -m qarau_pipeline benchmark --data D --out O` runs every reference video in
`pipeline/qarau_pipeline/benchmark_set.toml` through `run` (one child process per video,
per-video timeout, optional per-video frame cap) and writes `O/results.json` (`videos.<name>`: registered_pct, models,
reproj_px, points, scale_err_cm, time_s). Exit 0 only if every required and every present
optional video ran. The merge gate uses it; `--record` adds a per-commit row to
`docs/notes/benchmark.md`. Method: `docs/notes/benchmark-method.md`.

## Deployment shapes

| Shape | Who | What runs where |
| --- | --- | --- |
| Dev | us | everything on a laptop; GPU stages on Kaggle/Colab or a rented GPU |
| SaaS | private customers, contractors | our servers in Kazakhstan; tenant isolation |
| On-prem | state companies | one GPU server at the customer, installed by Ansible, no internet needed |
| Desktop | field engineers | Tauri app; quality check and preview locally, heavy stages on the customer server |
