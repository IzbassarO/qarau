# 0017 — Frame links for dense points: CSR, a per-point cap, two honest sources

- Status: accepted (dev team, P4b)
- Date: 2026-10-17

## Context

The evidence panel must show the source frames for any point it shows. Sparse points already
have them (COLMAP tracks, `tracks.*` in ADR 0003), but a dense cloud has 10–100× more points
and no tracks. COLMAP's `stereo_fusion` writes `fused.ply.vis` next to `fused.ply`: for each
fused point, the workspace images whose depth maps were merged into it. Other densifiers
(and the fake one in tests) give no such file. A dense point can be seen by dozens of frames;
storing all of them would make the links larger than the points.

## Decision

1. **CSR in the bundle.** Manifest key `dense` with `frame_offsets` (uint32, `count + 1`) and
   `frame_ids` (uint32, indices into `manifest.frames`, never COLMAP ids). Frames of point i
   are `frame_ids[frame_offsets[i]:frame_offsets[i+1]]`. Each entry declares `path`, `dtype`
   and `length`, so a reader can check sizes before using them. Same raw little-endian files
   as the rest of the bundle; no frame → points direction (not needed yet; cheap to add).
2. **Best first, capped.** Each row is sorted by the smallest angle between the point normal
   and the ray to the camera centre (the most frontal view first); without normals, by the
   nearest camera. Ties: lower frame index. At most `dense.max_frames_per_point` frames per
   point (`pipeline/config.yaml`, default 8): the bundle grows by at most 4 · (cap + 1) bytes
   per dense point, and the panel shows a handful of angles anyway. Dropped links are the
   worst-angle ones.
3. **Two sources, always named.** `dense.frame_links_source`:
   - `colmap_fusion`: parsed from `fused.ply.vis` (uint64 point count; per point uint32 n,
     then n uint32 workspace image indices). Index → frame by image name: the workspace
     lists the sparse model's registered images in ascending image id (COLMAP 4.x;
     `image_undistorter` keeps ids and names). Used whenever the `.vis` exists.
   - `sparse_tracks` (fallback): a cKDTree on the sparse points; a dense point inherits the
     union of the tracks of its 8 nearest sparse points within 5 × the median sparse
     nearest-neighbour spacing, kept only where it projects inside the frame with positive
     depth. No occlusion test: a link means "this camera saw this surface nearby and has the
     point in view", which is weaker than fusion. The UI must say which source it shows.
   `dense.densifier` is `fake` when the PLY carries the fake provider's header comment, so
   synthetic points are never presented as a reconstruction.
4. **Filtering in step.** Dense points go through the same sanitize (non-finite, extreme
   outliers) and far rules as sparse points; dropped points remove their link rows in the
   same pass (`filter_rows`), so row i always belongs to position i. Far dense points are
   dropped and counted, not added to the `far` layer.
5. **Version 2.** Export writes `version: 2`; every v1 key is unchanged and `dense` is
   optional, so the current viewer loads old and new bundles.

## Consequences

- The `.vis` index order is an assumption about COLMAP's workspace; it must be checked on
  the first real GPU run (P4a-2a-run): a fused point must reproject into its linked frames.
- The fallback misses frames whose sparse neighbours were not tracked there, and may keep
  a frame where the point is occluded. Good enough to find candidate frames; the panel shows
  the frame so the engineer judges.
- Licences: NumPy, SciPy (`cKDTree`), pycolmap/COLMAP are BSD-3-Clause; the `.vis` format
  is COLMAP's own output.
