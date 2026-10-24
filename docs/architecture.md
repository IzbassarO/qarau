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

Site configuration also stores an optional world-coordinate region of interest in
the run catalog. Its versioned JSON, coordinate-frame caveat, site resolution,
file layout, development API and FastAPI routes (task A7a) are documented
in [Region of interest — version 1](notes/roi-format.md).

## Workflow steps

One inspection run is a sequence of **steps** (`pipeline/qarau_pipeline/workflow.py`, ADR
0022): `probe → frames (+ quality report) → sparse (COLMAP) → export [→ dense]`. `dense` is
listed only when `pipeline/config.yaml` → `workflow.dense` is true (default false) and then uses
the fake provider only; a paid Modal run is never started from the workflow (ADR 0019).

**Contract.** A step is a plain callable `step(ctx) -> StepResult`.

- `StepContext`: `run_dir` (the run directory), `config` (config.yaml sections +
  `<run>/workflow.json`, what `run` was called with), `force`, `log`.
- `StepResult`: `status` (`ok` | `skipped` | `failed`), `outputs` (paths relative to the run
  dir), `metrics`, `error`.
- A step reads its inputs from the run dir (the probe document, `frames/frames.json`, the sparse
  model …) and writes its outputs under **`<run>/<step>/`**. It needs no network, no queue and no
  database; `run_steps` and `python -m qarau_pipeline step <name> --run <dir>` are the only
  callers.
- **Done marker.** When a step finishes, the runner writes `<run>/<step>/step.json` atomically
  (temp file + `os.replace`) with `name`, `status` (`done` | `failed`), `started_at`,
  `finished_at`, `duration_s`, `error`, `outputs`, `metrics`. A step whose marker says `done` is
  a **no-op** unless `force`; otherwise `<run>/<step>/` is **cleared** before the body runs, so a
  process killed mid-step leaves **no** marker and the next run redoes exactly that step from
  scratch. A step never writes outside its own folder (the uploaded video, `run.json`, `log.txt`
  and `workflow.json` at the run root belong to the run).
- `run_steps(run_dir, steps, from_step=None, force=False, on_event=…)` runs a slice in order
  and calls `on_event(name, "start" | "end", result)` around each step; it knows nothing about
  queues. `from_step` with `force` is the "rerun this step and the ones after it" operation.

**Runner.** Today the runner is **RQ on Valkey** (ADR 0012; `backend/app/workflow.py`): the job
`run_inspection(run_id, from_step)` spawns one `python -m qarau_pipeline step …` child per step
in the pipeline environment (the API environment never imports pycolmap) and, after every
transition, writes `Run.status` (`queued | running | done | failed`), `current_step`, `progress`
(finished steps / total, integer 0-100), `error`, `rq_job_id` and `Run.steps`, a JSON list with
one `{name, status (pending | running | done | failed), started_at, finished_at, duration_s,
error}` per step in workflow order (the shape `web/src/lib/processing.ts` parses), to Postgres,
each write in its own short transaction. Because the steps carry their own done markers,
re-queueing a run after a worker crash redoes only the unfinished step. **Temporal (or any other
orchestrator) can replace the RQ runner later without touching a step**: the step contract above
is the whole interface.

API (`backend/app/inspections.py`): `POST /api/inspections/{id}/runs` (engineer) queues the
inspection's newest run (the one B1's upload created; body `{run_id?, force?}`, 409 while
queued/running unless `force`, the escape after a crashed worker), `GET /api/inspections/{id}`
(viewer) returns the inspection with `run_id, progress, current_step, error, steps[]`, and
`POST /api/inspections/{id}/runs/{run_id}/steps/{step}/rerun` (engineer, 404 for an unknown
step) queues `from_step` with force for that step and the ones after it; earlier finished steps
keep their markers and their entries. Infra: `infra/docker-compose.yml` services `valkey`
(`valkey/valkey`, BSD-3) and `worker` (`infra/worker/Dockerfile`, `rq worker qarau`, mounts
`data/runs`); both the worker and the API (`make api`) read the queue address from `VALKEY_URL`
(`backend/app/settings.py` `valkey_url`, default `redis://localhost:6379/0`: the redis-py client
speaks the Redis protocol to Valkey, hence the scheme) and the worker also `qarau_pipeline_dir`.

**Run directory** (`pipeline/qarau_pipeline/layout.py`):

```
<run>/video.<ext>                 uploaded input (B1)        <run>/workflow.json  run parameters
<run>/probe/probe.json            + step.json                <run>/run.json       stage metrics (legacy names)
<run>/frames/000000.jpg …, manifest.json, frames.json, quality_report.json, step.json
<run>/sparse/database.db, masks/, sparse/0/*.bin             + step.json
<run>/export/web/manifest.json + typed arrays + thumbs/, cloud.laz, cloud.ply   + step.json
<run>/dense/fused.ply (+ .vis)    + step.json                <run>/log.txt        step output
```

Manifest paths stay relative to the run root (`export/web/points.position.f32`), which the API
(`/api/runs/{id}/files/{path}`) and the web app follow unchanged. Runs made before S1 keep
`frames.json`, `quality_report.json`, `colmap/` and `web/` at the run root; every `layout`
helper falls back to those paths when they exist, so old runs stay readable and re-exportable.

## Stage contracts

Each stage reads inputs from the run dir and writes outputs under `<run>/<step>/` (above). A
stage records `status`, `started_at`, `finished_at`, `tool_version`, `params` and `metrics` in
`<run>/run.json` (stage names `frames`, `reconstruction`, `export`) and the worker mirrors the
step state to `Run.steps`. Re-running a stage overwrites its own folder only. This
makes every result reproducible and every failure local.

| Stage | Inputs | Outputs |
| --- | --- | --- |
| frames | video (or image set) | `probe.probe_video()` → `VideoInfo` (codec, coded and display size, `rotation_deg` from the displaymatrix or `tags.rotate`, transfer/primaries/bit depth, `is_hdr`, fps, `is_vfr` from the pts of the first 120 packets: any interval > 25 % off the median; `frame_dt_s` = that median interval). Then one ffmpeg decode: autorotate (once, ffmpeg default; first frame checked against the display size), HDR (HLG/PQ) → 8-bit SDR BT.709 (`zscale`+`tonemap=hable` when the build has zscale, else `colorspace` with a warning), frames picked by presentation time with `select` on the same grid as the former `fps` filter (per tick k the last source frame before (k+½)/rate, taken as the first frame at or after that boundary minus the median frame interval; no duplicates, `-fps_mode passthrough`; rate `clamp(duration·6, 60, 300)` frames). Outputs: `frames/000000.jpg…` (long side ≤ 1920 px), `frames/manifest.json` (`source` VideoInfo, `conversions` e.g. `autorotate:90`, `tonemap:hable`/`tonemap:colorspace`, `vfr:pts_sampling`; `frames[{file, pts_s}]` = source pts; `warnings`), `frames.json` (`source`; per frame `i`, `file`, `t`, `blur`, `exposure{mean,clip_low,clip_high}`, `kept`, `drop_reason`; GPS later). The frame count above is the **budget** COLMAP gets; which frames fill it is decided by **parallax keyframes**, and the same pass writes the **footage quality report** `quality_report.json` next to `frames.json` (both: "Key techniques" below; the `quality` row is that report, run inside this stage). Unprobeable file, decode failure or 0 frames → `StageError` with a readable message |
| quality | frames.json | `quality.json`: usable frames, re-shoot list (time ranges + reason) |
| reconstruction | kept frames | Sky masks first (ADR 0013, classical OpenCV; off with `--no-sky-mask`): `colmap/masks/<image>.png`, 0 = sky, passed to feature extraction (`ImageReaderOptions.mask_path`). `colmap/database.db`, `colmap/sparse/0/*.bin` (main model; pycolmap in-process, `Reconstructor` interface; exhaustive matching ≤ 300 frames, else sequential with overlap 20 + quadratic overlap, no vocabulary tree; mapper `incremental` (default) or `global` (GLOMAP)). If the video splits into several models, it is joined again (ADR 0011 + P3b addendum): the frames the quality check dropped are added as temporary bridges, one GLOMAP pass maps everything, relaxed leftover registration follows if kept frames are still missing, the candidate with the most kept frames wins, bridges are deregistered and, if any were in the model, a global BA with Huber loss. Then the point filter (ADR 0013) deletes points whose max triangulation angle < 1.5° or whose mean reprojection error > the mapper's `filter_max_reproj_error` (4 px); `points_total`, `points_dropped_low_parallax`, `points_dropped_reproj` go to `run.json` and the benchmark. Models that still stand apart are written to `colmap/sparse/1..` and counted, never dropped silently; metrics: registered images, models, mapped model sizes, separate models, merge attempts/candidates, points, mean track length, mean reprojection error, seconds per step |
| export | `colmap/sparse/0`, `frames.json` | `cloud.laz` (xyz+rgb), `cloud.ply`, `web/` viewer bundle: `manifest.json` + raw typed arrays + `thumbs/` (format: ADR 0003, `docs/notes/m1-plan.md` §3); world is Z-up, centroid at origin, `world_from_colmap` stored. Before writing, points with a non-finite coordinate or farther from the per-axis median than max(100 × the 99th-percentile distance, 1) are dropped, counted in `manifest.stats.dropped_points` and printed. Points farther from the nearest registered camera than 2.5 × the median such distance form the `far` layer (`web/far.*`, manifest key `far`, ADR 0013); the object cloud, tracks, bounds and LAZ/PLY never contain them. With `--dense D`: the `dense` layer with per-point frame links (ADR 0017, below). Manifest `version` 2. LAS offset = middle of the kept extent, scale = max(1 mm, half extent / (2³¹−1) × 1.1), so quantized coordinates always fit in int32 |
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

**Parallax keyframes (frames stage, `keyframes.py`, ADR 0020).** Keyframes are chosen by
measured camera motion, not a fixed rate (`run --keyframes parallax`, default). ffmpeg
decodes `oversample` (1.5) × the frame budget as candidates (at most the source fps; same
HDR→SDR, rotation and VFR handling), the blur/exposure drops run, then KLT tracks
(`goodFeaturesToTrack` + pyramidal LK with a forward-backward check, 640 px long side) run
over all candidates. Walking the usable candidates, a new keyframe starts when, since the
last keyframe: the median residual after the best RANSAC homography (the parallax: ~0 for a
still camera and for turning on the spot) reaches θ · diagonal; or fewer than
`min_track_frac` (0.6) of its tracks survive (if the homography explains the motion, at most
every `rotation_min_gap_s`, 0.3 s); or `max_gap_s` (2 s) passed while the image moved
≥ `min_motion_frac` (1 % of the diagonal), so a still camera's near-duplicates never become
keyframes. θ starts at `parallax_frac` (0.0003, above a still camera's ~5e-5) and is raised
by log bisection until the keyframes fit the budget (`--max-frames` / 300 cap included);
track-loss keyframes alone over it → uniform subsample. First and last usable frame are
always kept. Other candidates get `kept=false`, `drop_reason="not_keyframe"` and are not
merge bridges (bridges = quality drops, at most 30 % of the keyframes). A budget capped
below 6 fps (long videos, `--max-frames`) runs as `fixed`: those frames are already sparse,
and moving them out of slow parts raised orono's reprojection 0.57 → 0.67 px.
`--keyframes fixed` keeps every usable frame of the budget (pre-P2b). All thresholds are
`KeyframeParams` fields; mode, thresholds and the fitted θ are in the frames stage metrics
(`keyframe_mode`, `keyframe_selection.parallax_frac_used`) and in the report.

**Footage quality report (frames stage, `quality.py`).** From the same candidates and
tracks, `<run>/frames/quality_report.json` (the table's `quality` row; version 2, full format:
`docs/notes/quality-report.md`): `version`; `summary` {`frames_total`, `keyframes`,
`issues_count`, `verdict` `ok` / `reshoot_recommended`}; `issues[]` {`kind`, `start_s`,
`end_s`, `frames` (flagged `frames.json` indices), `severity` `info` / `warn` (≥ 15 % of the
video, re-shoot), `metric`, `value`, `threshold`, `advice` (plain-English re-shoot
sentence), `advice_key` (`quality.advice.<kind>`, for the web's translations)}. Kinds:
`blur` (variance of the Laplacian), `too_fast` (flow or track loss over 1/6 s),
`pure_rotation` (the homography explains a 1 s window, no residual parallax), `low_texture`
(grid cells without a corner). `run` and `python -m qarau_pipeline quality --video V --out
O` (frames stage only) print a short summary.

**Dense reconstruction on a remote GPU (`dense_remote.py`, ADR 0016).** COLMAP dense
(`image_undistorter` → `patch_match_stereo` with geometric consistency → `stereo_fusion`)
needs CUDA, so it runs on a GPU worker from the image `infra/colmap-cuda` (COLMAP 4.2.1).
`RemoteDenseExecutor` drives any `GpuProvider` (start, upload frames + sparse model, run,
download `fused.ply` + `fused.ply.vis`, stop). Guarantees: a started worker is always stopped
(`finally`, 3 tries, then an error naming the worker id); one wall-clock deadline
`timeout_s` for the whole job, the remaining time passed to every call, `DenseTimeoutError`
past it; before any start, `used today + est_hours_per_job > daily_gpu_hours_budget` (UTC
day, JSON ledger under `data/`) raises `BudgetExceededError`. Config: `pipeline/config.yaml`
→ `dense_remote` (`enabled: false`, `provider: fake | modal`). Providers: a fake one for tests
and the Modal one (`dense_modal.py`, ADR 0019: injected client, lazy `modal` import, image
from `infra/colmap-cuda`, token only from `MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET`), which
nothing calls until the owner approves the paid account (P4a-2a-run); the default
`run`/`benchmark` path does not use either. `python -m qarau_pipeline dense-remote` runs the
fake by default; `--provider modal --allow-paid-gpu` is the only way to start a paid Modal run
(without the flag it exits before any Modal code), with the real ledger, budget and timeout.

**Dense points and their frame links (`visibility.py`, ADR 0017).** `python -m
qarau_pipeline export --sparse S --out O --dense D` reads `D/fused.ply` and adds the bundle
layer `manifest.dense` (`web/dense.position.f32`, `web/dense.color.u8`, same world frame as
`points`). Every dense point carries the frames that saw it as CSR: frames of point i =
`frame_ids[frame_offsets[i]:frame_offsets[i+1]]` (`web/dense.frame_offsets.u32`, N+1
entries; `web/dense.frame_ids.u32`, indices into `manifest.frames`), best first (smallest
angle between the point normal and the ray to the camera; nearest camera without normals),
at most `pipeline/config.yaml` → `dense.max_frames_per_point` (8). `dense.frame_links_source`
says how they were made: `colmap_fusion` (exact, from `D/fused.ply.vis`, workspace image
index → frame by image name) or `sparse_tracks` (fallback for a densifier without `.vis`:
union of the tracks of the 8 nearest sparse points within 5 × the median sparse spacing,
kept only where the point projects inside the frame with positive depth; no occlusion test).
`dense.densifier` is `fake` for the fake provider's synthetic points, which must never be
shown as a reconstruction. Dense points go through the same sanitize and far rules as the
sparse ones (far ones are dropped and counted in `dense.dropped`), their link rows filtered
in step. The sparse `points`/`tracks` arrays, `cloud.laz` and `cloud.ply` are unchanged.

**Manual findings (`findings.py` in the pipeline, `backend/app/findings.py`, ADR 0022).**
Until automatic detection returns, an engineer marks a finding through the API and the server
does the geometry. `POST /api/inspections/{id}/findings` (engineer): `class` (corrosion, crack,
leak, deformation, coating, other), `severity` (critical | watch), optional `comment`, and one
region: `polygon` (≥ 3 `[x, y, z]` map vertices) or `point_indices` (bundle `points` indices).
The server selects the bundle points inside the region (plane through the polygon, points within
5 % of its extent, even-odd test), measures `area_units` (convex hull in the points' best-fit
plane, bundle units²) and attaches evidence: the 3 frames from the bundle's point → frame links
that see the most selected points and are spread in viewpoint, each with the region projected as
an `outline` polygon in pixels (an `observation`, confidence 1.0). `area_m2` is filled only when
the inspection has a `ScaleReference` with `m_per_unit`; otherwise it is null and `scale` is
`"not set"`. 422 for a bad body or an empty region, 409 when the inspection has no exported run.
`GET /api/inspections/{id}/findings` and `GET /api/findings/{id}` (viewer) return class, severity,
status, area and scale, point indices with xyz, and the evidence frames with outline and
`image_url` (`/api/runs/<run>/files/frames/…`). `python -m qarau_pipeline finding-evidence --run R
--polygon JSON --out O` renders the same frames with the outline drawn (OpenCV) for a visual check.

## Benchmark

`python -m qarau_pipeline benchmark --data D --out O` runs every reference video in
`pipeline/qarau_pipeline/benchmark_set.toml` through `run` (one child process per video,
per-video timeout, optional per-video frame cap) and writes `O/results.json` (`videos.<name>`: registered_pct, models,
reproj_px, points, scale_err_cm, time_s). Exit 0 only if every required and every present
optional video ran. The merge gate uses it; `--record` adds a per-commit row to
`docs/notes/benchmark.md`. Method: `docs/notes/benchmark-method.md`.

`python -m qarau_pipeline refresh --data D --runs R --suffix S [--only a,b] [--report F]`
runs the same videos through the same sparse CPU `run` (never dense or `dense_remote`) and
exports each as a NEW viewer run `R/<video>-<S>` (e.g. `notre-dame-p3e`), so the web app lists
current results next to older runs. Append-only: a video whose target name exists fails before
any work and that path is never opened; each run is built in a temp dir outside `R` (`--work`,
same filesystem) and renamed into place when done, so a failed video leaves nothing in `R` and
the others still run. Nothing else is written into `R`. `created_at` in the manifest is the
export time (UTC). Prints a table (run, registered %, points, far-layer points, keyframes,
time, status), `--report` also writes it as Markdown; exit 1 if a video failed. Usage:
`docs/notes/refresh-runs.md`.

## Deployment shapes

| Shape | Who | What runs where |
| --- | --- | --- |
| Dev | us | everything on a laptop; GPU stages on Kaggle/Colab or a rented GPU |
| SaaS | private customers, contractors | our servers in Kazakhstan; tenant isolation |
| On-prem | state companies | one GPU server at the customer, installed by Ansible, no internet needed |
| Desktop | field engineers | Tauri app; quality check and preview locally, heavy stages on the customer server |
