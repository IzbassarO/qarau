# DEMO-cov: real coverage for the dev seed's demo inspection

## What the code does today (read first)

- S1 (`backend/app/workflow.py` `run_inspection`) runs the pipeline step `coverage` as a
  subprocess (`qarau_pipeline/workflow.py` `coverage` → `coverage.run_coverage` +
  `export.write_coverage_layer`), then `fill_coverage(run_id, run_dir)` replaces the run's
  `CoverageRegion` rows with `<run>/coverage/regions.json` (delete + insert, own transaction).
- The step does not take findings from the database: `finding_labels` reads the project step's
  `findings/points.finding.u16`. The demo finding is a manual one (`mark_finding`), which
  writes no such file, so the plain step would mark no region `finding`.
- `write_coverage_layer` rewrites `<run>/web/manifest.json` (and adds `points.coverage.u8`).
- The report (`report/inputs.py` `with_coverage`) reads `<run>/coverage/regions.json` of the
  inspection's last exported run; the API (`app/coverage.py`) reads the `CoverageRegion` rows.
- `seed_demo` returns early when the Demo site exists, so an existing dev database never got
  coverage.

## Choices

- `run_coverage` gets an optional `finding_points` (finding id → bundle point indices) that
  replaces `findings/points.finding.u16`. The seed passes its findings' `points`; nothing else
  changes in the computation (S1 passes nothing, so its output is byte-identical: pipeline
  test `test_regions_json_schema_on_a_synthetic_run` unchanged and green).
- The seed calls `run_coverage` in process (the backend already imports `qarau_pipeline` for
  findings; the step needs only NumPy) with `workflow.load_config()` (config.yaml thresholds),
  then the same `fill_coverage` as S1.
- **No overwrites in data/runs:** the seed writes `coverage/regions.json` only when the run has
  none (a new file); an existing one is read as is. It skips `write_coverage_layer`, which would
  rewrite the run's existing `manifest.json`: so notre-dame-p3e's bundle has no per-point
  coverage array (the viewer layer is COV-ui's; a real S1 run gets the array from the step).
- `ensure_demo_coverage` is called once, in `main()`, after `seed_demo`, for the Demo site's
  inspection whether it was just made or existed: one call site covers the backfill.
- Idempotent: rows already there → nothing computed, nothing written.
- Failure: any exception of the step prints `Coverage for 'Demo site' was not computed: the
  coverage step failed on run <dir> (<error>)`, stores no rows, the seed exits 0.

## Real data (notre-dame-p3e, 2026-10-29)

Re-run on the final code (round 2): a fresh scratch SQLite dev DB (`alembic upgrade head`),
`QARAU_ENV=dev`, `QARAU_RUNS_DIR=data/runs`, `python -m app.seed` twice. Printed output:

```
seeded organization 'dev' with users admin, engineer, viewer
seeded 'Demo site': run notre-dame-p3e, finding 1
coverage of 'Demo site': 485 regions: inspected_ok 192 (39.6%), finding 4 (0.8%), not_seen 289 (59.6%)
seeded organization 'dev' with users admin, engineer, viewer
'Demo site' not added: it exists, or notre-dame-p3e is not under QARAU_RUNS_DIR
coverage of 'Demo site': 485 regions: inspected_ok 192 (39.6%), finding 4 (0.8%), not_seen 289 (59.6%)
```

The run already had `coverage/regions.json` (added by round 1, the only file the task added
under the run), so the seed read it. To show it is what the final code computes, the same step
(`run_coverage` with the DB finding's points) was run again into a scratch dir:
`{'regions': 485, 'inspected_ok': 192, 'finding': 4, 'not_seen': 289}`, regions identical to
the run's file (`==` True).

- `GET /api/inspections/1/coverage`: computed=true, totals `{inspected_ok: 39.6, finding: 0.8,
  not_seen: 59.6}`, regions by state: inspected_ok 192, finding 4, not_seen 289.
- Demo report (`load_report_input` + `with_coverage` + `render_html`, en): **289 not-seen
  regions** (`Not inspected areas: 289.`), no "Coverage not computed.".
- `data/runs/notre-dame-p3e`: nothing existing changed.
