# P3b — sky mask and far-point filter: plan

Owner's review 2026-10-15: long outdoor shots grow a "sky wall" of far, badly triangulated
points that hides buildings and trees in the default view. Three independent fixes, one PR.

1. **Sky mask before features** — `pipeline/qarau_pipeline/sky.py`: classical OpenCV +
   NumPy detector (bright or blue/grey, low texture, connected to the top border), closed and
   dilated, safety caps (> 70 % of the frame or nothing touching the top → keep-all).
   PNGs in `<run>/colmap/masks/<image_name>.png` (COLMAP convention, 0 = ignore), passed to
   `pycolmap.extract_features` via `ImageReaderOptions.mask_path`. On by default,
   `run --no-sky-mask` turns it off. ADR 0013.
2. **Point filter after mapping** — `pipeline/qarau_pipeline/point_filter.py`, called by
   `ColmapReconstructor.reconstruct` before `sparse/0` is written: drop points whose max
   triangulation angle < 1.5° (low parallax), else whose mean reprojection error > the
   mapper's `filter_max_reproj_error` (read from pycolmap, 4 px). Counts
   `points_total`, `points_dropped_low_parallax`, `points_dropped_reproj` go to the
   reconstruction metrics in `run.json` and to every video in the benchmark `results.json`
   (and new columns in the `--record` markdown table).
3. **Far layer** — `export.py`: surviving points far from every camera (distance to the
   nearest registered camera centre > K × median of that distance) are written as
   `web/far.position.f32` / `web/far.color.u8` under manifest key `far`; the object arrays,
   tracks, bounds, LAZ/PLY hold only the rest. `web/src/lib/bundle.ts` reads only the keys it
   knows, so the viewer ignores `far` until A5. ADR 0013 + addendum to ADR 0003.

Tests: `test_sky.py` (synthetic sky IoU ≥ 0.9, textured → keep-all, all-sky → keep-all,
PNG naming), `test_point_filter.py` (exact bucket counts on a synthetic pycolmap model),
`test_export.py` (far layer), `test_benchmark.py` (new fields).

Verify: quick benchmark notre-dame + aegviidu; registered % must not drop below dev (100 %
both); inspect the far/object split numerically. K is tuned on those runs.
