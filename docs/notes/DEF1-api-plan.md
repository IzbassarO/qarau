# DEF1-api — Manual finding with evidence frames: plan

Date: 2026-10-23. Scope: `pipeline/`, `backend/`, `docs/`. No segmentation model, no decisions
(REV1-api), no UI, no coverage. Automatic detection is stopped until Kubernetes (owner,
2026-10-22): an engineer marks the region, the server does the geometry.

## What exists

- B1 tables `finding`, `observation`, `scale_reference` (migration 0001); `Run.run_dir` is the
  directory under `QARAU_RUNS_DIR`; `runs.py` serves any run file at
  `/api/runs/{run_id}/files/{path}`.
- The viewer bundle (ADR 0003): `points.position.f32` (bundle world, Z up, centroid at
  origin, no scale), per-frame `pose` (qvec, tvec: `x_cam = R x + t`) and `camera`
  (model, params, width, height), and the point → frame links as CSR
  `tracks.point_offsets.u32` / `tracks.point_frames.u16` (sparse tracks; the dense layer of
  ADR 0017 has the same CSR shape under `dense.*`). `data/runs/notre-dame-p3e` is a sparse run.

## Steps

1. **`pipeline/qarau_pipeline/cameras.py`** (new, NumPy only): `project` and `rot_from_qvec`
   move here from `export.py` (which re-imports them, so `visibility.py`, `projection.py` and
   the tests keep working). Reason: `export.py` imports pycolmap, laspy and cv2 at module level
   and the API process must not load them.
2. **`pipeline/qarau_pipeline/findings.py`** (new, NumPy only): `load_bundle(run_dir)`
   (positions, CSR links, registered cameras), `select_points`, `select_indices`,
   `region_area` (monotone-chain hull in the best-fit plane), `rank_frames` (greedy:
   most selected points, then count × (1 − max cosine to the chosen viewing directions)),
   `project_outline` (hull of the in-front projections, Sutherland–Hodgman clip to the image),
   and `evidence()` tying them together.
3. **CLI** `python -m qarau_pipeline finding-evidence --run R --polygon JSON --out DIR`:
   PNGs of the 3 frames with the outline drawn (OpenCV) + `finding.json`.
4. **Backend**: `qarau-pipeline` as a uv path dependency (`backend/pyproject.toml`,
   `uv.lock`); migration v0003 (`finding`: `area_units` becomes a Float in bundle units²,
   adds `comment`, `region`, `scale_source`; `observation`: `mask_path` nullable, adds
   `outline`); `backend/app/findings.py` with
   `POST /api/inspections/{id}/findings` (engineer), `GET /api/inspections/{id}/findings` and
   `GET /api/findings/{id}` (viewer), included in `main.py`.
5. **Tests**: `pipeline/tests/test_findings.py` (synthetic 3×2 grid, 4 cameras, hand-written
   links; literal expectations), `pipeline/tests/test_findings_real.py` (notre-dame-p3e,
   literal facade polygon, skipped when absent), `backend/tests/test_findings_api.py` (same
   fixture bundle in `tmp_path`, linked to inspection 1 through a `Run` row), OpenAPI and
   role lists updated.
6. **Docs**: ADR 0023 (region formats, tolerance, frame ranking, scale rule, path dependency),
   `docs/architecture.md` (findings API).
7. **Checks**: ruff, mypy, pytest in both projects; quick benchmark before and after.

## Decisions taken here

- Region → points: plane through the polygon vertices (SVD), even-odd test in that plane,
  distance to the plane ≤ 5 % of the polygon's largest in-plane extent (configurable `tol`).
- Area: convex hull of the selected points in their own best-fit plane, in bundle units².
  m² only when the inspection has a `ScaleReference` whose payload carries `m_per_unit`;
  otherwise `area_m2` is null and `scale` is `"not set"`.
- Evidence frames: ranked from the bundle's own point → frame links (the same CSR
  `visibility.py` produces for dense points); 3 frames, spread in viewpoint. Outlines are
  polygons in pixel coordinates (JSON), never raster masks, confidence 1.0 (manual).
- One run per POST: the inspection's most recent run that has a `web/manifest.json`; 409
  when there is none.
- Licences: NumPy (BSD-3), OpenCV (Apache-2.0, CLI render only), FastAPI / Pydantic /
  SQLAlchemy / Alembic / pytest (MIT). Convex hull and clipping are own code; no Open3D
  (the facade plane was fitted once with a NumPy RANSAC in scratch).
