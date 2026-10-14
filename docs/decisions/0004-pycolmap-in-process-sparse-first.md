# 0004 — Reconstruction via pycolmap in-process; sparse model only in M1

- Status: accepted
- Date: 2026-10-14

## Context

COLMAP (ADR 0001) can be driven through its CLI or through `pycolmap`, which ships
wheels for macOS arm64 and Linux (Python 3.12). Dense reconstruction (`patch_match_stereo`)
requires CUDA, which Apple Silicon does not have. The development machine is a 16 GB
M1 Pro.

## Decision

1. The `ColmapReconstructor` calls pycolmap directly (`extract_features`,
   `match_sequential`, `incremental_mapping`) — no subprocesses, options are typed, and the
   resulting `Reconstruction` is read back with the same library.
2. Video frames use `CameraMode.SINGLE` (shared intrinsics) and sequential matching with
   overlap 10 (quadratic). Unordered photo sets use exhaustive matching below 200 images.
3. M1 produces the sparse model only. Dense MVS is added in M2 on a CUDA machine (rented
   or Kaggle/Colab), behind the same `Reconstructor` interface.
4. Python dependencies are managed with `uv` (`backend/` and `pipeline/` are independent
   projects for now).

## Consequences

- `brew install colmap` is still useful for the GUI and for inspecting models, but the
  product path does not depend on the CLI binary.
- Sparse clouds are tens to hundreds of thousands of points; the viewer must already be
  built for millions so dense clouds plug in later without redesign.
