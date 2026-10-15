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
| frames | video (or image set) | `frames/000000.jpg…` (long side ≤ 1920 px, upright per the rotation tag, HDR (HLG/PQ) converted to SDR BT.709; rate `clamp(duration·6, 60, 300)` frames), `frames.json` (`source`; per frame `i`, `file`, `t` from ffmpeg `showinfo`, `blur`, `exposure{mean,clip_low,clip_high}`, `kept`, `drop_reason`; GPS later) |
| quality | frames.json | `quality.json`: usable frames, re-shoot list (time ranges + reason) |
| reconstruction | kept frames | `colmap/database.db`, `colmap/sparse/0/*.bin` (largest model; pycolmap in-process, `Reconstructor` interface; exhaustive matching ≤ 300 frames, else sequential + loop detection); metrics: registered images, points, mean track length, mean reprojection error, seconds per step |
| export | `colmap/sparse/0`, `frames.json` | `cloud.laz` (xyz+rgb), `cloud.ply`, `web/` viewer bundle: `manifest.json` + raw typed arrays + `thumbs/` (format: ADR 0003, `docs/notes/m1-plan.md` §3); world is Z-up, centroid at origin, `world_from_colmap` stored |
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
