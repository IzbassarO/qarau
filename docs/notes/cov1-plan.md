# COV1 — coverage: inspected_ok / finding / not_seen regions

Goal: every region of the sparse map gets exactly one coverage state (CLAUDE.md trust rules,
ADR 0002), stored as a file and as DB rows, served by the API, listed in the PDF report.
Not in this task: the viewer layer, dense-based coverage, comparing inspections (M4).

## Design

- **Regions** (`coverage.voxel_regions`): an axis-aligned voxel grid in bundle units, cell
  `floor(p / voxel_size)`; only occupied cells are regions. Region id = the integer voxel key
  `"ix_iy_iz"`; bounds = the cell, `[[ix, iy, iz] * size, ([ix, iy, iz] + 1) * size]`.
- **A frame counts for a region** when all hold:
  1. it is *usable*: registered, kept (not a quality-drop bridge) and in no quality-report
     issue of a kind listed in `coverage.unusable_issues` (blur, too_fast, low_texture);
  2. the region centroid is inside its frustum (`point_in_frustum`: COLMAP world-to-camera
     `x_c = R x + t`, depth in `(near, far]`, projection with K inside the image; far =
     `coverage.max_view_distance` or no limit);
  3. it observed at least one of the region's points (COLMAP tracks). The frustum alone
     cannot see a wall in between; a track proves the point was matched in that frame. This
     only removes frames, so it can only turn a region towards `not_seen`, never towards OK.
- **State**: `finding` if a point of a finding (`findings/points.finding.u16`, the project
  stage) is in the region; else `inspected_ok` if frame count >= `min_frames` and point count
  >= `min_points_per_region`; else `not_seen`. Too few frames, points or good frames never
  give `inspected_ok`.
- **nearest_frames**: up to 3 frame image names, nearest camera centre first, among the frames
  whose frustum contains the centroid (any quality); a region no frame looks at gets the 3
  nearest registered frames (where to re-shoot from).
- **Outputs**: `<run>/coverage/regions.json` (schema_version 1, voxel_size, thresholds,
  regions, totals = count and percent of regions per state) and
  `export/web/points.coverage.u8` (0 not_seen, 1 inspected_ok, 2 finding; manifest
  `points.coverage`, ADR 0003 addendum).
- **Workflow**: step `coverage` after export (after dense when dense is on, since dense
  re-exports the bundle); CLI `python -m qarau_pipeline coverage --run DIR` always recomputes
  (run it after `project` so new findings are counted).
- **Backend**: migration 0006 `coverage_region` (run FK, region_key, state CHECK, bounds JSON,
  frame_count, point_count, nearest_frames JSON, created_at; unique run+key). The worker
  imports regions.json after the coverage step (delete + insert per run: idempotent).
  `GET /api/inspections/{id}/coverage` (viewer): the latest run with rows; `not_computed`
  with a plain message when there are none.
- **Report**: section 5 lists the not_seen regions (count, bounds, nearest frames) from the DB
  rows; no rows: "Coverage not computed." (never "everything was seen").

## Known limits (accepted for this task)

- `voxel_size` is in bundle units, and COLMAP's scale differs per run; one value does not fit
  every video. Tuned on notre-dame below; a scale-relative size comes with a metric scale.
- Manual findings (DB, ADR 0023) do not flip a region to `finding`: the pipeline step cannot
  read the DB. Same for an engineer's reject. Add when coverage is recomputed on finding and
  decision changes.
- Radial distortion is ignored in the frustum test (K only): mild for phone/drone video.

## Results on real data

(filled in after the runs)
