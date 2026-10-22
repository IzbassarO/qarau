# COLMAP with CUDA for the remote dense stage (ADR 0016). Built locally, never pushed to a
# public registry. Same COLMAP version as the pipeline's pycolmap (4.2.1).
# COLMAP 4.x reads images with OpenImageIO, so FreeImage (GPL/FIPL) is not needed.
# CGAL (GPL-3 triangulation) is off: it only serves the Delaunay mesher, which we do not use.
# libopencv-core-dev: Ubuntu 24.04's OpenImageIO CMake config names /usr/include/opencv4
# without depending on it (first Modal build, 2026-10-21); headers only, OpenCV is Apache-2.0.

ARG CUDA_VERSION=12.6.3
ARG UBUNTU=ubuntu24.04

FROM nvidia/cuda:${CUDA_VERSION}-devel-${UBUNTU} AS builder
ARG COLMAP_TAG=4.2.1
# Turing (T4), Ampere (A100, A10/RTX 30xx), Ada (L4/L40/RTX 40xx), Hopper (H100).
ARG CUDA_ARCHITECTURES="75;80;86;89;90"
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        git cmake ninja-build build-essential ca-certificates \
        libboost-program-options-dev libboost-graph-dev libboost-system-dev \
        libeigen3-dev libopenimageio-dev openimageio-tools libmetis-dev \
        libopencv-core-dev \
        libgoogle-glog-dev libgflags-dev libgtest-dev libgmock-dev libsqlite3-dev \
        libglew-dev libceres-dev libsuitesparse-dev \
        libcurl4-openssl-dev libssl-dev \
    && rm -rf /var/lib/apt/lists/*
RUN git clone --depth 1 --branch ${COLMAP_TAG} https://github.com/colmap/colmap.git /src/colmap
RUN cmake -S /src/colmap -B /src/build -GNinja \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_INSTALL_PREFIX=/opt/colmap \
        -DCUDA_ENABLED=ON \
        -DCMAKE_CUDA_ARCHITECTURES="${CUDA_ARCHITECTURES}" \
        -DGUI_ENABLED=OFF \
        -DCGAL_ENABLED=OFF \
        -DTESTS_ENABLED=OFF \
    && cmake --build /src/build \
    && cmake --install /src/build
# Collect the shared libraries the binary links against (apt -dev packages stay behind).
# CUDA libraries come from the runtime base image; glibc is the same Ubuntu release there.
RUN mkdir /opt/colmap-libs \
    && ldd /opt/colmap/bin/colmap \
       | awk '/=> \// {print $3}' \
       | grep -v -E '^/usr/local/cuda|/(libc|libm|libdl|libpthread|librt)\.so' \
       | xargs -I{} cp -L {} /opt/colmap-libs/

FROM nvidia/cuda:${CUDA_VERSION}-runtime-${UBUNTU}
# Ubuntu 24.04 ships user `ubuntu` with UID 1000: removed so `colmap` keeps 1000 (host mounts).
COPY --from=builder /opt/colmap /opt/colmap
COPY --from=builder /opt/colmap-libs /opt/colmap-libs
RUN echo /opt/colmap-libs > /etc/ld.so.conf.d/colmap.conf && ldconfig \
    && userdel --remove ubuntu \
    && useradd --create-home --uid 1000 colmap \
    && mkdir /work && chown colmap:colmap /work
ENV PATH=/opt/colmap/bin:${PATH}
USER colmap
WORKDIR /work
ENTRYPOINT ["colmap"]
CMD ["-h"]
