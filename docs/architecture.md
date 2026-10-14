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
| frames | video | keyframes (JPEG), `frames.json` (timestamp, GPS, blur score, exposure score) |
| quality | frames.json | `quality.json`: usable frames, re-shoot list (time ranges + reason) |
| reconstruction | usable frames | `sparse/` (COLMAP model), `cloud.laz`, `poses.json`, `potree/` |
| segmentation | usable frames, prompts | `masks/<frame>.png`, `observations.json` (class, confidence, bbox) |
| projection | masks, poses, depth | `labels.npy` (per-point class/finding id) |
| measurement | labelled points, scale | `findings.json` (area m², extent, centroid) |
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

## Deployment shapes

| Shape | Who | What runs where |
| --- | --- | --- |
| Dev | us | everything on a laptop; GPU stages on Kaggle/Colab or a rented GPU |
| SaaS | private customers, contractors | our servers in Kazakhstan; tenant isolation |
| On-prem | state companies | one GPU server at the customer, installed by Ansible, no internet needed |
| Desktop | field engineers | Tauri app; quality check and preview locally, heavy stages on the customer server |
