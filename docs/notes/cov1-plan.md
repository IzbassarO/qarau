# COV1 (part 1) — coverage regions: inspected_ok / finding / not_seen

Goal: every region of the sparse map gets exactly one coverage state (CLAUDE.md trust rules),
written to `<run>/coverage/regions.json` by a `coverage` workflow step directly after `export`.
Format and rules: docs/architecture.md, "Coverage regions".

Out of scope (COV1-b): `CoverageRegion` table + migration, `GET /api/inspections/{id}/coverage`,
the bundle's per-point coverage array, the report's "Not-seen areas" section (round 1 of this
branch, commit c211cc7, built a first version of these: reuse it there). Also not: viewer layer,
dense-based coverage, comparing inspections (M4).

## Steps

1. `pipeline/config.yaml` → `coverage:` voxel_size, min_frames, min_frame_quality, min_points,
   near, far, unusable_issues. No thresholds in code.
2. `coverage.py`: `points_in_frustum`, `voxelize`, `classify_regions`, `run_coverage`. Poses
   come from the bundle manifest, which export already wrote in the gravity-aligned bundle frame
   (the same transform as the points), so no extra transform is needed.
3. Workflow step `coverage` after `export` (`product_steps`), CLI `coverage --run [--out]`.
4. Tests: synthetic frustum, one test per threshold at its boundary, findings, regions.json
   schema; `test_coverage_real.py` on notre-dame-p3e (skipped without the run).
5. Docs: architecture.md.

## Decisions

- A frame counts for a point only when the point is in its frustum **and** COLMAP tracked it in
  that frame: the frustum alone cannot see a wall in between. This only removes frames, so it
  can only move a region towards `not_seen`.
- Frame quality = the frame check's blur score (Laplacian variance, `frames[].blur`), plus the
  quality report's issues (`unusable_issues`); not-kept bridge frames never count.
- notre-dame-p3e has no project-stage findings; the real test uses the chapel-facade manual
  finding of test_findings_real.py (selected by `findings.select_points`), written into a tmp
  copy of the run (symlinks), never into the run itself.

## Known limits

- `voxel_size` is in bundle units; COLMAP's scale differs per run. Scale-relative size comes
  with a metric scale.
- Manual findings in the DB (ADR 0023) do not reach the step yet (COV1-b).
- Radial distortion ignored in the frustum test.
