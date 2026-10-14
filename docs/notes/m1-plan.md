# M0 + M1 plan — environment, video → point cloud, 3D viewer with video sync

Date: 2026-10-14. Owner of the plan: engineering (Claude Code session). Task: `docs/tasks/M1.md`.

## 0. Findings from research (what changed the plan)

- **MinIO is gone as a product we can ship.** The upstream repo says "no longer maintained",
  community edition is source-only, and `minio/minio` images are no longer on Docker Hub or
  Quay. We use an Apache-2.0 S3-compatible server instead (ADR 0006). Code only talks the
  S3 API, so the choice is swappable.
- **pycolmap 4.2.1 ships macOS arm64 wheels for Python 3.12** with feature extraction,
  sequential matching and incremental mapping in-process. No CUDA on Mac → sparse model
  only in M1; dense MVS later on a GPU box (ADR 0004). `brew install colmap` is also done
  (CLI + GUI for inspection).
- **COLMAP sample datasets moved** to GitHub releases
  (`github.com/colmap/colmap/releases/download/3.11.1/south-building.zip`). South Building:
  128 images + text sparse model → the viewer starts on it without waiting for the pipeline.
- **Public test videos (no own footage yet)** from Wikimedia Commons, all drone orbits of
  tall objects, in `data/samples/` (gitignored):
  - `aegviidu_water_tower.ogv` — 71 s, 1080p, CC0 (water tower, Estonia)
  - `orono_water_tower.webm` — 91 s, 1440p, CC BY 3.0 (water tower, USA)
  - `notre_dame_oubiels.webm` — 20 s, 640×480, CC0 (360° around a church; small, fast tests)
- Mac: Apple M1 Pro, 16 GB, Homebrew, Docker 29 + Compose 5.5, Node 26, pnpm 12, uv 0.12,
  Python 3.12 via `uv python install 3.12`, FFmpeg + COLMAP via Homebrew, `gh` logged in.

## 1. Code layout

```
backend/        uv project (FastAPI). app/{main,settings,health,runs,auth}.py, tests/
pipeline/       uv project (qarau_pipeline). stages: frames.py, reconstruction.py, export.py;
                interfaces.py (Reconstructor Protocol, StageResult), cli.py, tests/
web/            pnpm project (Vite + React 19 + TS + Three.js + Tailwind v4)
infra/          docker-compose.yml, postgres/Dockerfile (PostGIS + pgvector), .env.example
.github/workflows/ci.yml
Makefile        up, down, api, web, test, lint, sample (export South Building)
data/runs/<run_id>/   pipeline output, served by the API in dev (gitignored)
```

Two independent uv projects, not a workspace: the API does not import the pipeline yet.
When a worker process needs both, we switch to a uv workspace (one line change).

## 2. Pipeline run layout and stage contract

```
data/runs/<run_id>/
  run.json                 list of StageResult (name, status, started/finished, duration_s,
                           tool_version, params, metrics, error) — appended per stage
  frames/000000.jpg …      extracted frames, full resolution, numbered by time order
  frames.json              per frame: i, file, t (s), blur, exposure, kept, drop_reason
  colmap/database.db       COLMAP database
  colmap/sparse/0/         cameras.bin images.bin points3D.bin (largest model)
  cloud.ply, cloud.laz     exchange formats (PLY from COLMAP, LAZ via laspy)
  web/                     viewer bundle (section 3)
```

Stages are functions `run(inputs, out_dir, params) -> StageResult`; each writes only its own
outputs and is idempotent (re-run overwrites its own files). CLI:

```
python -m qarau_pipeline run    --video data/samples/x.mp4 --out data/runs/x [--max-frames 300]
python -m qarau_pipeline run    --images dir/ --out data/runs/x          # image set instead of video
python -m qarau_pipeline export --sparse dir/sparse --images dir/images --out data/runs/x
```

### frames
- `ffprobe` → duration, fps, size. Rate = `clamp(duration·2.5 fps, 60, 300 frames) / duration`.
  Extract with `ffmpeg -vf fps=R,scale=min(1920,iw):-2,showinfo -q:v 2`; parse `pts_time`
  from `showinfo` for exact timecodes (no arithmetic guessing).
- Scores (OpenCV, on a 640 px grey copy): blur = variance of Laplacian; exposure = mean,
  fraction of pixels < 10 and > 245.
- Drop rule (adaptive): blur < 0.35 × median blur → `drop_reason="blur"`; mean < 20 or
  > 235 or clipped > 40 % → `"exposure"`. Never drop more than 30 % (keep the best).
  Dropped frames stay in `frames.json` and in the viewer timeline (grey, "not used").

### reconstruction (`ColmapReconstructor`, the only `Reconstructor`)
- pycolmap in-process: `extract_features(camera_mode=SINGLE, SIMPLE_RADIAL,
  max_image_size=1600)` → `match_sequential(overlap=10, quadratic_overlap=True,
  loop_detection=False)` → `incremental_mapping()`; keep the largest model.
  SINGLE camera: all video frames share intrinsics — faster and far more stable.
- Metrics: registered images, points, mean track length, mean reprojection error, seconds.
- Image sets (South Building): same path with `camera_mode=AUTO`, `match_exhaustive` when
  ≤ 200 images (unordered photos), sequential otherwise.

### export (COLMAP model → exchange + viewer bundle)
- Read model with pycolmap (`Reconstruction.read`, binary or text).
- **Frame ↔ point correspondence, exactly from COLMAP:**
  - `images.bin`: pose per registered image (`cam_from_world` quaternion + translation),
    camera id, and its 2D points with `point3D_id` → **frame → points**.
  - `points3D.bin`: xyz, rgb, `track` = list of (image_id, point2D_idx) → **point → frames**.
  - Image name `000123.jpg` → `frames.json[123].t` → timecode. Nothing is interpolated.
  - `first_seen[p] = min(frame index over track)`; per-frame visible sets stored as CSR
    (offsets + indices) in both directions.
- **Up vector / height:** COLMAP's world is arbitrary. Up = mean of camera "up" axes
  (world vector of each camera's −y axis); if nearly degenerate (nadir flight), fall back to
  the normal of the plane fitted to camera centres. Rotate so up → +Z, translate the point
  centroid to the origin, no scaling. Apply the same rigid transform to all poses. Store
  `world_from_colmap` (4×4) in the manifest so nothing is lost. Height = z.
- Write `cloud.laz` (laspy, xyz + rgb), thumbnails (≤ 960 px wide JPEG), the bundle.
- PotreeConverter is not installed and not needed at sparse sizes (ADR 0005); skipped.

## 3. Viewer bundle v1 — the pipeline ↔ viewer contract (ADR 0003)

`<run>/web/manifest.json` + raw little-endian typed arrays (no header; dtype in the suffix):

```jsonc
{
  "version": 1,
  "run_id": "aegviidu",
  "source": {"kind": "video" | "images", "name": "aegviidu_water_tower.ogv",
             "duration_s": 70.94, "fps": 29.97, "width": 1920, "height": 1080},
  "world": {"up": [0,0,1], "world_from_colmap": [16 numbers, row-major 4×4],
            "bounds": {"min": [x,y,z], "max": [x,y,z]}, "z_range": [p2, p98]},
  "points": {"count": N,
             "position": "web/points.position.f32", // N×3 xyz interleaved
             "color": "web/points.color.u8",        // N×3 rgb from COLMAP
             "first_seen": "web/points.first_seen.u16", // frame index; 65535 = never observed
             "num_obs": "web/points.num_obs.u8"},   // track length, clamped to 255
  "tracks": {"frame_offsets": "web/tracks.frame_offsets.u32", // n_frames+1
             "frame_points": "web/tracks.frame_points.u32",   // point indices, ascending per frame
             "point_offsets": "web/tracks.point_offsets.u32", // n_points+1
             "point_frames": "web/tracks.point_frames.u16"},  // frame indices, ascending per point
  "frames": [ // one entry per extracted frame, ascending t, index == position in array
    {"i": 0, "t": 0.0, "image": "frames/000000.jpg", "thumb": "web/thumbs/000000.jpg",
     "kept": true, "drop_reason": null, "blur": 142.1,
     "registered": true,
     "camera": {"width": 1920, "height": 1080, "model": "SIMPLE_RADIAL",
                "params": [f, cx, cy, k], "K": [fx, fy, cx, cy]},
     "pose": {"qvec": [w,x,y,z], "tvec": [x,y,z]},   // cam_from_world, aligned world
     "center": [x,y,z], "n_points": 1234}
  ],
  "stats": {"frames_extracted": 177, "frames_dropped": 12, "frames_registered": 160,
            "points": 54321, "mean_track_length": 5.1, "mean_reproj_error_px": 0.9,
            "processing_s": {"frames": 12.3, "reconstruction": 410.2, "export": 3.1}}
}
```

Conventions the viewer relies on:
- **All paths in the manifest are relative to the run directory** (`data/runs/<run_id>/`), and
  the API serves that directory: viewer URL = `/api/runs/{run_id}/files/` + path.
- Pose is COLMAP `cam_from_world`: `x_cam = R(q)·x_world + t`; camera centre `C = −Rᵀt`.
  COLMAP camera axes: +x right, +y down, +z forward. three.js camera: +x right, +y up,
  −z forward → `R_three = Rᵀ · diag(1, −1, −1)`; vertical FOV = `2·atan(H / (2·fy))`.
- Unregistered or dropped frames have `registered: false`, no pose, empty CSR range.
- Frame indices everywhere are indices into `frames` (time order), never COLMAP image ids.

Why raw arrays and not PLY/LAZ/Potree for the viewer: `fetch().arrayBuffer()` →
`BufferAttribute` with zero parsing, supports our per-point attributes (`first_seen`),
and 5 M points is 60 MB of positions — fine for M1. LAZ stays the exchange format.

## 4. API (M0 + what M1 needs)

- `GET /health` → `{status, db, storage}`; 503 when a dependency is down.
- `GET /api/runs` → list of run ids with `source` + `stats` from each manifest.
- `GET /api/runs/{run_id}/manifest.json`, `GET /api/runs/{run_id}/files/{path}` (static;
  path-traversal safe; from `QARAU_RUNS_DIR`, default `data/runs`).
- Every route declares a role via a dependency (`require_role("viewer")`). Dev mode (no
  `QARAU_API_TOKEN`) lets everything through; with a token set, `Authorization: Bearer`
  is required. Health is the only unauthenticated route. Real users/roles come in M5.
- Settings via pydantic-settings from `.env` (`infra/.env.example` committed).

## 5. Viewer (web)

- Vite + React 19 + TS, Tailwind v4, three.js (plain, no react-three-fiber: one canvas,
  imperative render loop, custom shader), TanStack Query for the two fetches,
  react-router for `/` (runs) and `/viewer/:runId`, tiny `t()` i18n with ru/kk/en JSON.
- **Rendering:** one `THREE.Points` + `ShaderMaterial`. Attributes: `position`, `color`,
  `firstSeen`, `visibleNow` (u8). Uniforms: `currentFrame`, `buildUp`, `showCurrent`,
  `pointSize`, `zRange`, ramp colours, `colorMode` (height | rgb). Point size attenuates
  with distance. State changes = uniform updates; only `visibleNow` is re-uploaded on
  frame change (one `Uint8Array` fill from the CSR slice, < 5 ms for 1 M points).
- **Camera:** `OrbitControls` with damping + `zoomToCursor`; right-drag or Shift+drag pans;
  double-click raycasts the clicked point and re-targets; toolbar reset / top / side /
  through-camera; keys F, Home, 1/3/7. "Through camera" sets position = `center`,
  orientation from the pose, FOV from `fy`, target = centre + forward · d.
- **Path and frustums:** one `Line` for the travelled path (`setDrawRange`) + dashed line
  for the rest; `LineSegments` for every N-th frustum; the current frustum drawn larger
  in `#5B9BFF`. XYZ gizmo in a corner viewport (scissor).
- **Timeline:** native `<input type="range">` over frame indices, timecode from `frames[i].t`,
  play = step frames in real time. Right panel shows `thumbs/` (the real extracted frame),
  camera altitude (z), points visible in this frame, "map assembled so far" =
  `#points with first_seen ≤ i / count`, toggles, run stats.
- **Point → frames:** click a point → CSR slice → list of timecodes → click one jumps the
  video and flies the camera to that pose (tween over 400 ms).
- Picking: CPU projection of all points with the view-projection matrix (≈ 1 M points in
  ~15 ms), nearest within 8 px, depth-aware. GPU picking later if dense clouds need it.
- Dropped / unregistered frames shown grey on the timeline; the thumb still displays.

## 6. Performance plan (5 M points @ 60 fps target)

Measured, not assumed: a synthetic 5 M-point bundle (`web/scripts/synthetic-bundle.mjs`)
is rendered in real Chrome and FPS is read from a `requestAnimationFrame` counter; results
go to `docs/notes/m1-results.md`. Expected: a single draw call of 5 M `GL_POINTS` on an
Apple GPU is fine; memory 60 MB positions + 15 MB colours. If a real dense cloud later
exceeds the budget, add Potree octree LOD (ADR 0005 records the trigger).

## 7. Verification

- Backend: `pytest` with the DB/storage probes patched (200 and 503 shapes), auth
  dependency, runs listing + path traversal. `make up && make api` → green `/health`.
- Pipeline: synthetic 3 s video (ffmpeg `testsrc`) through `frames`; synthetic
  2-camera/3-point reconstruction through `export` (CSR consistency, `first_seen`,
  pose round-trip); `@slow` end-to-end on the 20 s church video.
- Viewer: Vitest for pose conversion and CSR helpers; Playwright screenshots of South
  Building and each video run, looked at by a human-like eye (me), attached to the PR.
- Three real videos run end-to-end; timings + failures in `docs/notes/m1-results.md`.

## 8. Risks

| Risk | Mitigation |
| --- | --- |
| COLMAP fails on a video (shiny water tower, sky, repetitive texture) | Lower `peak_threshold`, more features, SINGLE camera; report "could not reconstruct" honestly in run.json |
| CPU-only SIFT is slow (minutes per video) | `max_image_size=1600`, ≤ 300 frames; acceptable for M1; GPU box later |
| Up-vector estimate wrong for nadir drone footage | Plane-fit fallback; manifest stores the transform so the viewer can override later |
| Sparse clouds are thin (50–200 k points), not "millions" | Honest for M1; dense MVS needs CUDA (M2 on a GPU machine); perf test with synthetic 5 M |
| Headless Chromium renders WebGL via SwiftShader (slow) | Screenshots in headless are fine; FPS measured in real Chrome |
| MinIO withdrawal | ADR 0006; S3 API only; owner confirms on-prem choice later |

## 9. Work split (parallel subagents, each in its own git worktree)

- **A — environment + API:** infra/, backend/, Makefile, CI, .env.example, M0 docs.
- **B — pipeline:** pipeline/, export first (South Building bundle at
  `data/runs/south-building/`), then frames + reconstruction, runs on the three videos.
- **C — viewer:** web/, starting on a synthetic bundle, switching to South Building, then
  the video runs.
Integration, review, screenshots, PR: this session.
