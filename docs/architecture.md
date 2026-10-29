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
0022): `probe → frames (+ quality report) → sparse (COLMAP) → export → coverage [→ dense]`
(coverage: "Coverage regions" below). `dense` is
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
The export also leaves a byte-identical copy of the manifest at the pre-S1 path
`<run>/web/manifest.json` for the web app's dev-only stand-in for the API (`web/vite-dev-runs.ts`,
screenshots and e2e without a backend), which still reads it there; the API and `layout` ignore
the copy while `export/web/manifest.json` exists, and it is not part of the stored bundle.

## Storage: S3 is the system of record, `data/runs` is a cache

(ADR 0024; `pipeline/qarau_pipeline/storage.py`, `backend/app/storage.py`.) Every run artefact
lives in one S3-compatible bucket, one prefix per inspection, five **kinds**:

```
<org>/<site>/<inspection>/video/<filename>        the uploaded video, immutable
<org>/<site>/<inspection>/frames/…                 000000.jpg …, manifest.json, frames.json,
                                                   quality_report.json, step.json
<org>/<site>/<inspection>/sparse/…                 database.db, masks/, sparse/0/*.bin, step.json
<org>/<site>/<inspection>/dense/…                  fused.ply (+ .vis), step.json
<org>/<site>/<inspection>/bundle/…                 web/manifest.json + typed arrays + thumbs/,
                                                   cloud.laz, cloud.ply, step.json
```

`<org>`, `<site>` and `<inspection>` are the catalog ids (`1/1/1`). A run outside the catalog
(the CLI, the benchmark, `refresh`) uses org `_local`, site `_local` and the run directory name
as inspection (`_local/_local/notre-dame`). The key of a run-relative path is a fixed mapping
(`layout.KIND_DIRS`): `export/web/manifest.json → bundle/web/manifest.json`, `frames/000012.jpg →
frames/000012.jpg`, `sparse/… → sparse/…`, `dense/… → dense/…`; nothing else of the run dir
(`probe/`, `run.json`, `log.txt`, `workflow.json`) is stored: `probe` is recomputed in under a
second from the video, the rest is bookkeeping of one machine.

**Immutable and rebuilt.** The **video** is written once: `put` refuses to overwrite a key under
`video/`, the pipeline uploads it only when the prefix is empty, and a second upload with the same
file name for the same inspection is a 409. **Frames** are the evidence every finding links to
and are never touched by a later step; only a forced rerun of the `frames` step (new parameters)
replaces them. **sparse**, **dense** and **bundle** are derived and rebuilt freely: a rerun of a
step replaces its whole prefix (objects it no longer produces are deleted), so storage always
mirrors the step's last run, and the dense step re-uploads the bundle it re-exported.

**The cache.** `QARAU_RUNS_DIR/<run>` is the pipeline's working copy. Before a step runs,
`run_step` pulls the kinds it reads (`frames` needs `video`; `sparse` needs `frames`; `export` and
`dense` need `frames` and `sparse`) and its own kind whenever the local dir lacks a `done` marker;
after the marker is written it pushes the kinds it made (`frames → frames/`, `sparse → sparse/`,
`export → bundle/`, `dense → dense/ + bundle/`, `probe → video/`, once). The marker travels with
the kind, so `rm -rf data/runs/<run>` followed by a re-queue downloads every finished step and
reruns only `probe`; a push that fails marks the step `failed` (an output that never reached
storage does not count as done). The worker passes the run's prefix on the command line
(`python -m qarau_pipeline step <name> --run DIR --prefix 1/1/1`), so it survives a wiped cache;
`run` and `step` without `--prefix` use `_local/_local/<run>`. With **`QARAU_S3_BUCKET` unset
nothing is pulled or pushed and boto3 is never called**: the local-only mode of the benchmark and
the tests.

**Configuration** (`infra/.env.example`): `QARAU_S3_ENDPOINT` (empty for AWS S3),
`QARAU_S3_BUCKET` (empty = off), `QARAU_S3_ACCESS_KEY`, `QARAU_S3_SECRET_KEY`, `QARAU_S3_REGION`.
The same five variables drive the pipeline worker and the API; dev points them at the compose
`s3` service (Versity, ADR 0006), the cloud at the provider's S3, on-prem at the customer's
gateway. The client uses path-style addressing and SigV4, which every S3-compatible server
accepts. The bucket is created if missing.

**Serving** (`backend/app/runs.py`). `GET /api/runs/{id}/manifest.json` and
`GET /api/runs/{id}/files/{path}` keep their paths, roles (viewer) and shapes in every mode:

1. the file is in the local cache → served from disk as before;
2. otherwise, `QARAU_FILES_MODE=stream` (default) → the API streams the object from the bucket
   (`Cache-Control: public, max-age=3600`, media type by extension);
3. `QARAU_FILES_MODE=redirect` → `302 Location: <signed URL>`, valid `QARAU_SIGNED_URL_TTL_S`
   seconds (default 300). The browser then fetches the bucket directly, so the **bucket must
   allow CORS from the web origin**; that is why `stream` is the default and the web viewer needs
   no change for either mode.

A run id is mapped to its prefix through the catalog (`Run → Inspection → Site.org_id`) or, for
an id without a `run` row, to `_local/_local/<id>`; the path is turned into a key only through
`KIND_DIRS` and never with an empty, `.` or `..` part, so traversal stays rejected without a
local directory to resolve against. `GET /api/runs` (the legacy catalog) still lists only the
cache; the inspections API is the catalog of record. Legacy runs with `web/` at the root have no
storage copy and are served from the cache only.

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
| coverage | bundle points + poses + tracks, frame blur scores, quality report, `findings/points.finding.u16` | `coverage/regions.json`: one voxel region per occupied cell, state inspected_ok / finding / not_seen ("Coverage regions" below); the step then adds `points.coverage.u8` + manifest key `coverage` to the bundle, and the worker stores the regions in `coverage_region`. CLI `python -m qarau_pipeline coverage --run DIR [--out DIR]` |
| risk ranking | findings, changes, asset data | severity + reason text per finding |
| report | the API database: inspection, site, organization, findings with observations (evidence frame + outline), decisions, scale source | PDF only so far (RPT1, ADR 0027): `qarau_pipeline.report.generate(inspection_id, lang, report_time, engine, runs_dir)` → `Generated(pdf, sha256, input)`, called by both the CLI and `GET /api/inspections/{id}/report.pdf` ("Inspection report" below); CLI `python -m qarau_pipeline report --inspection N --lang ru\|kk\|en --out F [--report-time ISO] [--db URL] [--runs-dir DIR]` (runs dir default `QARAU_RUNS_DIR`; without one: no map, no frame images, said in the PDF). `report/snapshot.py` (ADR 0032) draws the map of the inspection's last exported run with a NumPy point rasterizer (orthographic, fixed camera 30° above the filmed side, z-buffer, PNG via Pillow, no GL) and reads each finding's first 3 evidence frames; the template puts numbered SVG markers over the map, a crop around the marker in each card and the outline as an SVG `<polygon>` over each frame (viewBox in frame pixels). `report/inputs.py` reads the tables with SQLAlchemy Core into a frozen `ReportInput` (status/class/severity replayed from the decision log by `replay_decisions`, the same function `backend/app/decisions.py` uses; report number, version, version history and verification ID; the report time); `report/render.py` (`render_report(input, lang, runs_dir)`, reads only the run dirs) fills the Jinja2 template `report/templates/report.html.j2` + `report.css` and WeasyPrint writes an A4 PDF/A-3b: cover (company, site, asset, capture date, report number, version), executive summary, map snapshot with numbered findings, findings table by severity, one card per finding (class, severity, status, map location, area in m² with uncertainty or "uncertainty not estimated", or "scale not set"; scale source; the 3 evidence frames with outlines; who decided and when; its decision log), not inspected areas (the not_seen regions of the map's run with their nearest frames, "Coverage regions" below; "coverage not computed" without regions.json), method and limitations, sign-off (prepared / checked / approved), document control (version history, verification ID); `@page` header with the report number and footer with the engineer, report time and "page X of Y" on every page. All strings in `report/translations.toml` (ru/kk/en), v8 fonts embedded from `report/fonts/` (OFL). The report time is the only clock in the file (PDF and XMP dates), so the same input gives byte-identical files. XLSX and work orders: later |

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

**Coverage regions (COV1, `pipeline/qarau_pipeline/coverage.py`).** The `coverage` step runs
directly after `export`, on the sparse bundle. Every threshold is in `pipeline/config.yaml` →
`coverage:`; none is in code.

- Regions: a voxel grid of `voxel_size` (bundle units: COLMAP's arbitrary scale, after export's
  gravity alignment) over the bundle's points; only occupied cells are regions, id `"i_j_k"` =
  `floor(p / voxel_size)`.
- A frame **sees** a point when the point is in its frustum (bundle pose, pinhole K from the
  manifest, `near < depth < far`, projection inside the image; radial distortion ignored) and
  the frame tracked it in COLMAP (matched there, so no wall in between).
- A frame is **usable** when it is registered, kept, in no quality-report issue of a kind in
  `unusable_issues` and its blur score is `>= min_frame_quality` (an image set has no score:
  its frames count).
- State: `finding` when a point of a finding (`findings/points.finding.u16`, the project stage)
  is in the region; else `inspected_ok` when `>= min_frames` usable frames see one of its points
  and it holds `>= min_points` points; else `not_seen`. `not_seen` is never folded into OK.
- `<run>/coverage/regions.json` (written atomically: temp file + `os.replace`):

  ```json
  {"version": 1, "units": "bundle", "voxel_size": 1.0,
   "thresholds": {"min_frames": 3, "min_frame_quality": 100.0, "min_points": 20,
                  "near": 0.01, "far": null, "unusable_issues": ["blur", "too_fast", "low_texture"]},
   "summary": {"inspected_ok": 196, "finding": 0, "not_seen": 289},
   "regions": [{"id": "0_-1_0", "bounds": {"min": [0, -1, 0], "max": [1, 0, 1]},
                "state": "inspected_ok", "frame_count": 87, "point_count": 1204,
                "finding_ids": []}]}
  ```

- **Database (COV1-b).** Table `coverage_region` (migration 0006, model `CoverageRegion`): `id`,
  `run_id` → `run` (organization through run → inspection → site, as `finding`), `region_key`
  (the regions.json id), `state` (CHECK inspected_ok | finding | not_seen), `frame_count`,
  `bounds_min` / `bounds_max` (JSON [x, y, z], bundle units), `created_at`; unique
  (`run_id`, `region_key`). The worker (`backend/app/workflow.py` `fill_coverage`) replaces a
  run's rows after every done `coverage` step: delete + insert in one transaction, so a rerun
  never duplicates rows. A missing or unreadable regions.json leaves no rows and a log warning;
  the run does not fail.
- **API.** `GET /api/inspections/{id}/coverage` (viewer): `{regions: [{id, state, frame_count,
  bounds: {min, max}}], totals: {inspected_ok, finding, not_seen}, computed}` for the
  inspection's newest run (by `created_at`) that has rows, regions ordered by id. **Totals are
  percentages of the region count** (each region counts once whatever its size or point count;
  1 decimal), the simplest share an engineer can check against the list. No rows: `computed:
  false` and empty `totals`, never zeros that read as OK. 404 for an unknown inspection or, for a
  user token, one of another organization (the shared read-only dev token sees all, as on every
  route).
- **Bundle.** `points.coverage.u8` + manifest key `coverage` (ADR 0003 addendum): one code per
  bundle point, 0 not_seen, 1 inspected_ok, 2 finding, 255 no region; a point on a face shared by
  regions takes the first region by id. `export.write_coverage_layer` writes it at the end of
  `export_bundle` and again in the `coverage` step (which runs after export, so the step also
  pushes the `bundle` storage kind). No regions.json: `coverage: {computed: false}`, no array.
- **Report.** "Not inspected areas" lists the not_seen regions of the map's run (the last
  exported one): their count and a table of id, both corners (2 decimals, map units: the sparse
  map has no metric scale, so the report never calls them metres) and the 3 frames whose camera
  centres (bundle poses) are nearest to the region's centre: where to re-shoot from. "Coverage not
  computed" only when the run has no regions.json.
- `coverage/` is not a storage kind (a wiped cache recomputes it). With `workflow.dense` on,
  coverage stays on the sparse map.

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

**Manual findings (`findings.py` in the pipeline, `backend/app/findings.py`, ADR 0023).**
Until automatic detection returns, an engineer marks a finding through the API and the server
does the geometry. `POST /api/inspections/{id}/findings` (engineer): `class` (corrosion, crack,
leak, deformation, coating, other), `severity` (critical | watch), optional `comment`, and one
region: `polygon` (≥ 3 `[x, y, z]` map vertices) or `point_indices` (bundle `points` indices).
The server selects the bundle points inside the region (plane through the polygon, points within
5 % of its extent, even-odd test with edges inclusive), measures `area_units` (convex hull in the points' best-fit
plane, bundle units²) and attaches evidence: the 3 frames from the bundle's point → frame links
that see the most selected points and are spread in viewpoint, each with the region projected as
an `outline` polygon in pixels (an `observation`, confidence 1.0). `area_m2` is filled only when
the inspection has a `ScaleReference` with `m_per_unit`; otherwise it is null and `scale` is
`"not set"`. 422 for a bad body or an empty region, 409 when the inspection has no exported run.
`GET /api/inspections/{id}/findings` and `GET /api/findings/{id}` (viewer) return class, severity,
status, area and scale, point indices with xyz, and the evidence frames with outline and
`image_url` (`/api/runs/<run>/files/frames/…`). `python -m qarau_pipeline finding-evidence --run R
--polygon JSON --out O` renders the same frames with the outline drawn (OpenCV) for a visual check.

**Decision log (`backend/app/decisions.py`, ADR 0025).** An engineer's verdict on a finding is
a row in `decision`, and only ever a new row. `POST /api/findings/{id}/decisions` (engineer, 201):
`verdict` `confirm` | `reject` | `reclass`; `comment` (required for reject and reclass); `new_class`
and/or `new_severity`, carried by `reclass` only (at least one of them). The user comes from the
bearer token and `created_at` from the server; the body cannot set them. Every POST inserts exactly
one `decision` row and one `audit_log` row (action `finding.decision`, the user, the route with
the finding id, `object_id` = the decision id) in the same transaction, with no dedup: the same body twice is two rows. 404 for an unknown finding, 422 for an
inconsistent body. `GET /api/findings/{id}/decisions` (viewer) returns the whole history oldest
first (`created_at`, then id): user name, verdict, comment, new class / severity, time. There is
no PUT, PATCH or DELETE route (405), and the database refuses UPDATE and DELETE on `decision`
through the trigger of migration 0001 (Postgres `qarau_append_only()`, SQLite `RAISE(ABORT)`), so
a bug or a direct SQL session cannot edit the log either. Migration 0004 adds the two `reclass`
columns with plain `ADD COLUMN`, never batch mode, so the trigger survives.

*Derived status.* The `finding` row keeps the values as proposed (`status = proposed`, the class
and severity given at marking). Every finding response (`GET /api/findings/{id}`, the inspection
list) computes `status`, `class` and `severity` by replaying the finding's decisions oldest first
(`derive_finding_state`): the status is that of the latest decision, `reject` → `rejected`,
`confirm` or `reclass` → `confirmed`; a `reclass` sets the class and/or severity it carries and a
field it does not carry keeps its previous value; later decisions keep the reclassified values.
No decision → the proposed values. Nothing is written back to `finding` or to an earlier decision.

**Inspection report (`backend/app/reports.py`, RPT1, ADR 0027).**
`GET /api/inspections/{id}/report.pdf?lang=ru|kk|en` (viewer) returns the PDF/A report
(`application/pdf`, `Content-Disposition: inline; filename="<number>-v<version>-<lang>.pdf"`).
It calls the same `qarau_pipeline.report.generate` as the CLI with `QARAU_RUNS_DIR` and the
report time from the `report_clock` dependency (now, server zone, whole seconds; tests override
it), so the CLI with the same `--report-time` writes byte-identical files. Headers
`X-Report-SHA256` (hex of the body) and `X-Report-Generated-At` (the report time, ISO 8601, the
only clock in the PDF). Every generation inserts a row in `report` (migration 0005: inspection,
lang, sha256, `generated_at` in UTC, report number, version, `created_by` = the user, null for the
shared read-only token); the row never feeds back into the PDF, so the same data, language and
report time always give the same hash. 404 for an unknown inspection, 422 for a missing or unknown
`lang`, 401 without a valid token. Every report is version 1 until reports are issued (signed off).

**Event organizations and guests (`backend/app/event.py`, DEMO1, ADR 0034).** For a public demo
(LaunchPad, 2026-11-06) guests sign in with a short event code instead of a user account.
`uv run python -m app.event create --name launchpad --expires 2026-11-06T23:59-05:00 --max-guests
300 --runs <id>,<id>` is the only way to make an **event organization**: it checks that every run
exists and is `done`, then in one transaction inserts the organization (default name "LaunchPad
demo") with `is_event = true`, an `event_code` row (name, SHA-256 of the code, `expires_at` in
UTC, `max_guests`) and one `event_code_run` row per shared run, and prints the code once
(8 characters from `secrets`, alphabet without 0/O/1/I/L). Bad input exits 2 and writes nothing.
Migration 0007 holds the rules in the database: `organization.is_event` is set only at INSERT
(a trigger rejects any UPDATE that changes it; no API body or seed sets it), `event_code` and
`guest` rows are refused outside an event organization, and `finding`, `decision` and `report` get
a nullable `guest_id` (the guest's sandbox). The decision trigger is reworked: UPDATE is always
refused; DELETE only for a decision whose `guest_id` is set and whose user belongs to that
guest's event organization, so `app.event purge` can remove guest rows and nothing else.
`audit_log` stays fully append-only everywhere: a code use is logged by a user that purge keeps.
Part 2 adds the guest sign-in (401 for an unknown or expired code, rate limits per IP and per
code, each use in the audit log), the `guest` role (view shared runs, mark, decide, get the PDF;
no upload, processing or other organizations; another guest's rows are 404) and `purge`.

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
