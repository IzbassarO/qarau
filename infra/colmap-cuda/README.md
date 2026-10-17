# colmap-cuda

COLMAP 4.2.1 built with CUDA, for the remote dense stage (`image_undistorter` →
`patch_match_stereo` → `stereo_fusion`; ADR 0016). PatchMatch stereo needs CUDA, so this is
the image a GPU worker runs. The pipeline's own sparse path stays on pycolmap (CPU).

- Multi-stage: `nvidia/cuda:12.6.3-devel-ubuntu24.04` builds COLMAP from the pinned
  `4.2.1` tag with `CUDA_ENABLED=ON` and `CMAKE_CUDA_ARCHITECTURES=75;80;86;89;90`
  (T4, A100, A10/RTX 30xx, L4/RTX 40xx, H100); `nvidia/cuda:12.6.3-runtime-ubuntu24.04`
  gets only the install and the shared libraries the binary links against.
- Runs as the non-root user `colmap`, workdir `/work`, `ENTRYPOINT ["colmap"]`.
- Not built in CI (a CUDA build takes too long for it). Needs an x86-64 machine with Docker;
  running needs an NVIDIA GPU and the NVIDIA Container Toolkit.

## Build

```bash
docker build -t qarau/colmap-cuda:4.2.1 infra/colmap-cuda
# other GPUs: --build-arg CUDA_ARCHITECTURES="86"   (smaller, faster build)
```

## Smoke test

```bash
docker run --rm --gpus all qarau/colmap-cuda:4.2.1 -h          # prints "with CUDA"
docker run --rm --gpus all -v "$PWD/run:/work" qarau/colmap-cuda:4.2.1 \
  patch_match_stereo --workspace_path /work/dense --workspace_format COLMAP
```

`colmap -h` must report `with CUDA`; without `--gpus all` dense commands fail with a CUDA
error, which is expected.

## Distribution

The image is **never pushed to a public registry**. It contains NVIDIA CUDA runtime
libraries (NVIDIA Deep Learning Container licence + CUDA EULA: commercial use allowed,
runtime redistribution under the EULA's terms). Publishing it anywhere, even to a private
registry of a GPU provider, is an owner decision (see ADR 0016).

Licences of what is inside: COLMAP BSD-3-Clause; Ceres BSD-3; Eigen MPL-2.0; Boost BSL-1.0;
OpenImageIO Apache-2.0; SQLite public domain; glog/gflags BSD-3; METIS Apache-2.0;
SuiteSparse (CHOLMOD, LGPL-2.1+, dynamically linked). Built with `CGAL_ENABLED=OFF`: CGAL's
triangulation is GPL-3 and only serves COLMAP's Delaunay mesher, which we do not use. Not
built with FreeImage (COLMAP 4.x uses OpenImageIO). Whatever COLMAP's CMake fetches at build
time (e.g. PoseLib, FAISS) is BSD/MIT; recheck the list when the tag changes.
