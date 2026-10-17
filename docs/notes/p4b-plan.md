# P4b — frame links for dense points: plan

Goal: every dense point in the viewer bundle carries the frames that saw it, so the evidence
panel can show source frames for any point. Web side is a later task.

## What exists (read first)

- `export.py` writes only the sparse model: `points.*` + `tracks.*` CSR (ADR 0003). There is
  no dense layer in the bundle yet.
- `dense_remote.py` (P4a-1) downloads COLMAP's `fused.ply` + `fused.ply.vis`; nothing reads
  them. `dense_fake.py` is the fake GPU provider; its fusion writes an empty PLY.
- So P4b also adds the smallest dense layer that can carry the links: `manifest.dense`.

## Steps

1. `interfaces.py`: `DensePoints` (xyz, rgb, normals?, CSR `frame_offsets`/`frame_ids`,
   `links_source`).
2. `visibility.py`:
   - `read_vis` (COLMAP `fused.ply.vis`: u64 count, per point u32 n + n u32 image indices),
     `read_ply` (binary little-endian vertex PLY, COLMAP fusion layout).
   - `.vis` indices → frame index by image name (workspace image order = registered images
     of the uploaded sparse model, ascending image id).
   - fallback `sparse_track_links`: cKDTree on sparse points, union of the tracks of the k=8
     nearest sparse points within 5 × median sparse spacing, kept only where the dense point
     projects inside the image with positive depth.
   - `best_first`: sort each row (normal·view direction if normals, else camera distance),
     cap at `dense.max_frames_per_point` (config.yaml, default 8).
   - `load_dense(dir, rec, frames)`: `.vis` present → `colmap_fusion`, else `sparse_tracks`.
3. `dense_fake.py`: fake fusion writes a real `fused.ply` (jittered sparse points + normals)
   and a `.vis` from the sparse tracks, so the e2e path runs without a GPU.
4. `export.py`: optional `dense` → sanitize + far filter applied to points and CSR rows in
   step, aligned like `points`, written as `web/dense.*`; manifest `version` 2,
   `dense = {count, position, color, frame_links_source, max_frames_per_point,
   frame_offsets{path,dtype,length}, frame_ids{...}}`. Every existing key unchanged.
   CLI: `export --dense DIR`.
5. Docs: ADR 0017 (CSR, cap, fallback), ADR 0003 addendum, architecture.md row.
6. Tests: `test_visibility.py`, `test_export.py` dense cases, `test_e2e.py` fake-dense path
   (fast synthetic + slow real video), ≥ 95 % of points linked.
7. Verify: quick benchmark (notre-dame, room), then fake dense + export on both outputs;
   coverage %, median links, bundle size; look at linked frames for one point.
