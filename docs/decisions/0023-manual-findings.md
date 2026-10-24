# 0023 — Manual findings: region formats, area in bundle units, evidence frames by links

- Status: accepted (dev team, DEF1-api)
- Date: 2026-10-23

## Context

Automatic defect detection (SAM segmentation and projection, DEF2) is stopped until the
Kubernetes setup (owner, 2026-10-22). Until then an engineer marks defects by hand, and the
product still owes the trust rules: every finding links to evidence frames, sizes are never
shown in metres without a scale, and nothing is a black box. B1 created the `finding`,
`observation` and `scale_reference` tables but no route. The viewer bundle (ADR 0003) already
holds, for every point, the frames that saw it (`tracks.point_offsets` / `point_frames`; the
dense layer of ADR 0017 has the same CSR shape).

## Decision

1. **Two region formats, one result.** `POST /api/inspections/{id}/findings` takes either a
   `polygon` (≥ 3 `[x, y, z]` vertices in map coordinates) or `point_indices` (indices into the
   bundle's `points` layer), never both. The server turns a polygon into points: a plane is
   fitted through the vertices (SVD), the map points within a tolerance of that plane are
   projected onto it and tested against the polygon (even-odd rule; a point on an edge counts
   as inside, so a polygon drawn through map points keeps them). Tolerance: 5 % of the
   polygon's largest in-plane extent (`PLANE_TOL_FRAC`; a parameter of `select_points`, not of
   the API yet). A region that selects no point, a collinear polygon, out-of-range or repeated
   indices are 422. The finding stores both the region as marked (`region`) and the selected
   indices (`points`), so the choice is reproducible.
2. **Area in bundle units², metres only with a scale.** `area_units` is the area of the convex
   hull of the selected points in their own best-fit plane (own monotone chain, no new
   dependency; fewer than 3 or collinear points give 0). `area_m2 = area_units × m_per_unit²`
   only when the inspection has a `ScaleReference` whose payload carries `m_per_unit` (the
   newest such reference wins; `scale_source` records `"<kind>: <source>"`). Otherwise
   `area_m2` is null and the response says `scale: "not set"`. No field ever holds a metre value
   without a scale (checked by a backend test). The payload key is a contract for P5a-1, which
   creates scale references.
3. **Evidence frames from the point → frame links.** The 3 frames (`N_EVIDENCE`) come from the
   bundle's CSR links, not from a new visibility test: the first frame sees the most selected
   points; each next one maximises `count × (1 − max cosine)` between its viewing direction to
   the region centroid and the directions of the frames already chosen, so three frames from the
   same spot are not three pieces of evidence. Ties go to the lower frame index; frames that see
   no selected point are never used. Each frame gets the region projected into it as an
   **outline polygon** in pixels (convex hull of the in-front projections, Sutherland–Hodgman
   clipped to the image), stored as an `observation` with `outline` JSON, `mask_path` null and
   `confidence` 1.0 (manual). Raster masks stay for model output.
4. **Geometry in the pipeline package, loaded by the API.** `pipeline/qarau_pipeline/findings.py`
   (NumPy only) and `cameras.py` (`project`, `rot_from_qvec`, moved out of `export.py` so the
   API never imports pycolmap, cv2 or laspy) are used by `backend/app/findings.py` through a uv
   path dependency (`qarau-pipeline`, editable; `pipeline/pyproject.toml` gained a hatchling
   build backend and `py.typed`). The API still runs no pipeline stage; it reads a finished
   bundle. The CLI `finding-evidence` renders the same result with OpenCV for visual checks.
5. **Which run.** The finding belongs to the inspection's exported run (a viewer manifest
   under `export/web/`, or `web/` for a pre-S1 run: ADR 0022 layout); several exported runs →
   the last by id, until runs carry a date. No exported run → 409 with a readable message.
6. **Schema.** Migration 0003: `finding.area_units` becomes a Float (it was a unit label string
   in 0001 and the table was empty), plus `comment`, `region`, `scale_source`;
   `observation.mask_path` nullable plus `outline`. 0001 is unchanged.

## Consequences

- Sparse bundles link a point to the ~7 frames that triangulated it, so "most selected points
  seen" is a vote over sparse observations; it still picks the frontal, well-spread frames on
  real footage (notre-dame-p3e facade: frames 52, 140, 96; renders in
  `data/findings-check/notre-dame-p3e/` from `tests/test_findings_real.py`). Dense bundles (ADR 0017) can be
  wired in by reading `dense.*` instead of `points`/`tracks` when the UI marks on dense points.
- The outline is the hull of the whole region, clipped: parts hidden behind other geometry are
  still outlined (no occlusion test). The engineer sees the frame and judges; strong radial
  distortion (|k1| ≳ 0.1) would need undistortion before the hull.
- Decisions (confirm / reject) are REV1-api; the finding's `status` stays `proposed` here.
- Licences: NumPy (BSD-3), OpenCV (Apache-2.0, CLI render only), hatchling (MIT), FastAPI,
  Pydantic, SQLAlchemy, Alembic, pytest (MIT). No model is involved.
