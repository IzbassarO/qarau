# P5a — metric scale and gravity (plan)

Scope: pipeline + docs. The user's marks come as a JSON file
(`docs/notes/scale-reference-format.md`); the web marking UI is a later dev_app task.

1. `pipeline/qarau_pipeline/scale.py`
   - `load_reference(path)`: Pydantic v1-format validation (kind, point count, ≥ 2
     observations, finite numbers, `distance_m` only and always for `distance`).
   - `triangulate()`: multi-view DLT on undistorted normalized coordinates
     (`Camera.cam_from_img`) with the poses of `colmap/sparse/0`. Unknown / unregistered frame
     → `StageError`.
   - `distance_scale`, `a4_scale` (least squares over 4 edges + 2 diagonals, both edge
     orders tried, RMS residual in cm).
   - `gravity()`: camera estimate (`export.estimate_up`, mean image-down axis), refined by a
     NumPy RANSAC plane on the sparse points: largest plane below the cameras whose normal is
     within 30° of the estimate; else the camera estimate. Source recorded.
   - `Alignment` = one similarity `x' = s·A·x + T` (up → +Z, floor → z = 0, centred in x/y),
     plus `transform_pose()` for cameras. Reprojection unchanged by construction.
2. `export_bundle(..., ref=None)` computes the alignment instead of its own rotation and
   centroid; writes `world.metric/scale_source/scale_factor/gravity_source/up_axis/
   scale_error_cm`; `world_from_colmap` now includes the scale. Point counts unchanged.
3. CLI: `run --scale-ref J`, `scale --run R --ref J`. The reference is validated, then copied
   to `<run>/scale_ref.json`; export reads it from there, always from the untouched
   `colmap/sparse/0`, so a rerun never compounds. `run` without `--scale-ref` removes a stale
   copy. `project` uses 1 m/unit when the bundle is metric.
4. Benchmark: optional `scale_reference` (path or inline table) per manifest entry →
   `run --scale-ref`; `scale_err_cm` from the export metrics. `benchmark_set.toml` itself is a
   protected file: no room reference is added there; `benchmark-method.md` says how.
5. Tests `test_scale.py` + one benchmark test. ADR 0018, architecture row, format note.
6. ruff, mypy, pytest; quick benchmark notre-dame + room; check the exports are upright.
