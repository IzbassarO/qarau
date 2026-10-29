# COV1-b plan: coverage regions in the database, API, bundle and report

Part 2 of the COV1 split (owner 2026-10-28). COV1 writes `<run>/coverage/regions.json`
(`pipeline/qarau_pipeline/coverage.py`, unchanged here): `{version, units: "bundle", voxel_size,
thresholds, summary, regions: [{id "i_j_k", bounds {min, max}, state, frame_count, point_count,
finding_ids}]}`. Bounds are in bundle units (the sparse map has no metric scale).

## Build

1. **Table** `coverage_region` (migration `v0006_coverage_regions.py`): id, run_id → run (org
   scoping through run → inspection → site → organization, like `finding`), region_key, state
   (CHECK inspected_ok | finding | not_seen), frame_count, bounds_min / bounds_max (JSON [x, y, z]),
   created_at; unique (run_id, region_key). Downgrade drops it.
2. **Fill** `backend/app/workflow.py` `fill_coverage(run_id, run_dir)`: after the `coverage` step
   is done, delete the run's rows and insert the regions of `regions.json` in one transaction.
   Missing or unreadable file: no rows, a warning in the log, the run goes on. `coverage/` is not a
   storage kind, so the file is read from the run's cache dir, where the step just wrote it.
3. **API** `GET /api/inspections/{id}/coverage` (viewer, `backend/app/coverage.py`): regions of the
   inspection's newest run that has rows, totals in % of the region count (1 decimal),
   `computed: false` and empty totals when there are none. A user token only sees inspections of
   its own organization (404 otherwise); the shared read-only dev token sees all, as on every
   other route.
4. **Bundle** `export.py` `write_coverage_layer(run)`: `points.coverage.u8` (0 not_seen,
   1 inspected_ok, 2 finding, 255 no region; first region by id when several contain a point) and
   the manifest key `coverage {computed, path, dtype, count, codes, regions}`; without
   `regions.json` the key is `{computed: false}` and there is no array. Called at the end of
   `export_bundle` and by the `coverage` step (export runs before coverage, so the step adds the
   array to the fresh bundle; the step now also pushes the `bundle` kind). ADR 0003 addendum.
5. **Report**: `inputs.load_coverage(run_dir)` reads `regions.json` and the bundle's camera poses
   (`cameras.rot_from_qvec`); up to 3 nearest frames per not-seen region by camera centre to the
   region centre (NumPy). `render_report` fills `ReportInput.coverage` from the map's run (the last
   exported one, as the map). The section lists the count and a table (region, min and max corner
   with 2 decimals, nearest frames). The corners are labelled as map units, not metres: the
   sparse map has no scale, and calling them metres would be a false claim (trust rules).
   "Coverage not computed" stays only when `regions.json` is missing. New strings in ru, kk, en.

## Tests

- `backend/tests/test_migrations.py`: table, columns, check constraint; downgrade to 0005 drops it,
  upgrade brings it back.
- `backend/tests/test_coverage_api.py` with `tests/fixtures/coverage/regions.json` (2 inspected_ok,
  1 finding, 1 not_seen): fill twice → 4 rows; the workflow job fills after the coverage step;
  missing file → no rows; API totals 50.0 / 25.0 / 25.0, 401 without a token, 404 for another
  organization, computed false without rows; OpenAPI lists the route.
- `pipeline/tests/test_export.py`: 6 literal points + 2 regions → `[1, 1, 0, 0, 2, 255]` read
  through the manifest; `export_bundle` without regions says not computed.
- `pipeline/tests/test_report.py`: 2 not-seen regions with literal bounds and a literal camera set,
  ru / kk / en HTML has the heading, the count, the bounds and the frame names, not "coverage not
  computed".

## Real data

notre-dame-p3e has no `coverage/` yet: run COV1 on it with `out_dir` in the scratch dir (the run is
only read), copy the run to scratch, export there, render ru / kk / en reports from a scratch
SQLite database seeded with one inspection on that run.
