# P4a-1 — Remote dense stage executor (no money spent)

Goal: COLMAP dense (undistort → PatchMatch → fusion) on a rented GPU later, without ever
leaking a running worker or overspending. This task builds the image and the executor and
tests them with a fake provider only. ADR 0016.

1. `infra/colmap-cuda/Dockerfile`: multi-stage. Builder `nvidia/cuda:12.6.3-devel-ubuntu24.04`,
   COLMAP `4.2.1` (same version as our pycolmap; uses OpenImageIO, no FreeImage),
   `CUDA_ENABLED=ON`, `CMAKE_CUDA_ARCHITECTURES=75;80;86;89;90`, GUI off. Runtime
   `nvidia/cuda:12.6.3-runtime-ubuntu24.04` + the shared libraries `ldd` reports for the
   binary. Non-root user, `ENTRYPOINT ["colmap"]`. Ubuntu 24.04 rather than 22.04: COLMAP 4.x
   upstream builds on it (CMake ≥ 3.28, newer Ceres/OpenImageIO). Built locally only, never
   pushed, not in CI.
2. `pipeline/qarau_pipeline/dense_remote.py`: `WorkerSpec`, `WorkerHandle`, `GpuProvider`
   protocol (every call gets the remaining seconds), `RemoteDenseExecutor`, `GpuLedger`,
   `dense_commands()`, `load_dense_remote_config()`. Order in `run()`: enabled? → budget
   check → reserve the estimate in the ledger → start → try: upload, 3 COLMAP commands,
   download fused.ply + .vis; finally: stop (retried 3×, then an error naming the worker) →
   settle the ledger with the actual hours.
3. `pipeline/qarau_pipeline/dense_fake.py`: `FakeClock`, `FakeGpuProvider` (local dirs,
   records calls, injectable failure / hang / exit code / failing stop). No network, no
   subprocess.
4. `pipeline/config.yaml`, `dense_remote` section, `enabled: false`. PyYAML (MIT) added.
5. CLI `dense-remote --provider fake` for a visible round-trip; its ledger lives in `--out`.
6. Tests `pipeline/tests/test_dense_remote.py`; ADR 0016; `docs/architecture.md` section.
7. Checks + the quick benchmark (default path must be unchanged: nothing imports the
   executor outside the CLI command).
