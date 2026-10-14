# 0008 — Dense geometry without CUDA: monocular depth scaled to the sparse model

- Status: accepted (research path; revisit when a GPU machine is available)
- Date: 2026-10-14

## Context

COLMAP's dense stage (`patch_match_stereo`) needs CUDA (ADR 0004). The dev machine is an
M1 Pro. A sparse cloud (50-100 k points) is too thin to put defect masks on (M2). We need
millions of points on a laptop, with per-point frame tracks so that "seen now", build-up
and findings keep working, without a paid service.

## Options considered

| Option | Verdict |
| --- | --- |
| COLMAP MVS on a free GPU (Colab / Kaggle) | Best geometry (true multi-view), BSD. But manual upload of customer-like footage, session limits, not reproducible in CI or on a customer server without a GPU box. Keep as the product path once a CUDA worker exists. |
| OpenMVS (CPU) | MPL-2.0 / AGPL parts; CPU densification of 226 frames takes hours and needs a dense-capable build; not shipped for macOS arm64 wheels. Not chosen. |
| VGGT / MASt3R / DUSt3R | Weights CC-BY-NC (VGGT) or non-commercial (DUSt3R/MASt3R). Research only, forbidden on the product path (CLAUDE.md). |
| Depth Anything V2 Base / Large | CC-BY-NC-4.0. **Not used.** |
| **Depth Anything V2 Small + scale/shift to sparse points** | Chosen. Runs on MPS/CPU, Apache-2.0 code and weights. Depth is relative, so every frame is fitted to that frame's own COLMAP points, which makes it metric-consistent with the reconstruction. |
| Depth Pro / Metric3D / UniDepth | Metric depth directly, but heavier and licences were not cleared; Apple Depth Pro weights are research-only. Not chosen. |

## Licences of everything used

| Component | Licence | Note |
| --- | --- | --- |
| `depth-anything/Depth-Anything-V2-Small-hf` (weights) | Apache-2.0 | Only the Small model is Apache. Base/Large are CC-BY-NC and must not be loaded. The default model id is pinned to Small. |
| `transformers`, `torch` (ml group) | Apache-2.0, BSD-3 | Optional dependency group; the package imports without it. |
| OpenCV, NumPy, pycolmap | Apache-2.0, BSD, BSD | already in the product path |

## Decision

`qarau_pipeline.dense.DepthAnythingDensifier` implements `Densifier`
(`densify(run_dir, out_dir) -> metrics`), CLI `python -m qarau_pipeline dense --run <dir>`
(needs `uv run --group ml`). Per registered frame:

1. Depth Anything V2 Small -> relative inverse depth.
2. Robust fit `1/z = a*d + b` to the frame's sparse points (RANSAC on 2 points, least squares
   on inliers, 20 % of the points held out). The frame is **rejected** if the held-out median
   relative depth error exceeds 10 % or the slope is not positive. Rejected frames are listed
   in `dense.json` (shown, not hidden).
3. Pixels whose depth jumps at edges (flying pixels) and sky-far pixels are dropped.
4. Multi-view consistency: each pixel is reprojected into up to 8 neighbouring frames (view
   angle < 40 deg, every 2nd of the 16 nearest for baseline); kept only if >= 3 of them have a
   depth within 2 % of the reprojected depth. The agreeing frames become the point's track.
5. Voxel fusion across frames (voxel = 0.13 % of the scene diagonal, earlier frame wins).

Models stay behind the `Densifier` interface; a CUDA MVS implementation can replace this.

### Bundle bound (export, bundle v2)

Raw output is 7.1 M points x up to 9 frames: a 400+ MB bundle, too much for a browser.
`export --dense` therefore applies one rule (constants `MAX_DENSE`, `MAX_TRACK` in `export.py`):

- **at most 2 000 000 dense points**, uniform random subsample with a fixed seed (order and
  alignment preserved, so the export is reproducible);
- **at most 4 frames per dense point** (the earliest 4 in the sorted track), so `first_seen`
  stays exact and `num_obs` is capped at 4 + 1 for dense points.

Sparse points always keep their full tracks. `points.source` marks 0 = sparse, 1 = dense;
`manifest.dense` records method, frames used and the number of dense points kept; the
manifest version is 2. v1 bundles (no `source`, no `dense`) still open (checked on `orono`).

## Measured (Orono water tower, 226 frames, Apple M1 Pro, MPS)

| Metric | Orono | Notre-Dame (640x480 web video) |
| --- | --- | --- |
| frames used / rejected | 204 / 22 | 44 / 8 |
| dense points after fusion | 7 116 352 | 649 283 |
| held-out depth error (median / p90) | 4.3 % / 5.9 % | 7.4 % |
| pixels passing consistency | 91 % (10.5 M of 11.5 M) | n/a (see dense.json) |
| track length (median / max) | 7 / 9 | |
| stage time | model 82 s, depth+fit 146 s, fuse 556 s (about 13 min total) | about 1 min |
| bundle after cap | 2 101 189 points, 148 MB (tracks 63 MB, positions 25 MB) | 664 522 points, 41 MB |
| viewer load (Chromium, Metal GPU, local dev server) | 1.5-3.2 s | n/a |
| fps orbiting | 60 (vsync cap) | |

Screenshots: `docs/notes/m2/dense-orono-*.jpg` (scrub at frame 30, through-camera views at
frames 30 and 110 match the video frame: horizon, trees, road). Build-up (dark = not yet
filmed) and "seen now" (brightened) work on dense points because they come from the same
CSR as sparse points.

## Known limits

- Monocular depth is only as good as the per-frame fit: error is measured against sparse
  points, which sit on textured surfaces. Textureless surfaces (sky-like metal, water) are
  interpolated by the network, not measured. Always an estimate, not survey geometry.
- Far scene (aerial footage) becomes a thin sheet: depth is trusted up to 3x the 98th
  percentile of the frame's sparse depth.
- The scale is the COLMAP scale (relative units until a reference is set); measurement
  (m2) must not treat dense areas as more accurate than the sparse model.
- Fusion is a first-wins voxel filter, not a probabilistic merge; no normals.
- 2 M cap on the web side means 1 point in about 3.5 is shown on Orono; the full 7 M cloud
  stays in `dense/` for projection and measurement. For tiled/LOD loading see ADR 0005.
- 556 s fusion step is O(frames x neighbours) NumPy; can be cut with `--frame-stride 2`.
- Needs the `ml` group; pycolmap and torch both bundle libomp on macOS, handled with
  `KMP_DUPLICATE_LIB_OK` (unsupported by Intel, works in practice for inference only).
