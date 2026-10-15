# P1 — benchmark command: plan

How a video becomes a model today: `python -m qarau_pipeline run --video V --out O` runs
`frames` (ffmpeg + OpenCV scores → `O/frames/`, `frames.json`), `reconstruction`
(pycolmap in-process, `ColmapReconstructor.reconstruct` → `O/colmap/database.db`,
`O/colmap/sparse/0` = largest model) and `export` (`cloud.laz`, `web/`). Every stage writes
its metrics to `O/run.json`; the reconstruction metrics already hold `images_in`,
`registered_images`, `models`, `points`, `mean_reproj_error_px` of the largest model.

Benchmark = run that exact command per video, read `run.json`. Nothing new to compute.

- `pipeline/qarau_pipeline/benchmark_set.toml` — manifest (name, file, required, notes,
  optional `max_frames` profile), read with `tomllib`. 4 public videos required,
  `IMG_8315.MOV` optional. Aegviidu/Orono capped at 150 frames after the first run timed out
  (see `benchmark-method.md`).
- `pipeline/qarau_pipeline/benchmark.py` — `run_benchmark(data, out, only, timeout_s,
  record, repo_root)`. Each video runs `python -m qarau_pipeline run` in a **child
  process** with `subprocess.run(timeout=...)`: pycolmap cannot be interrupted in-process,
  and a COLMAP crash must not take the other videos down. Logs → `out/<name>/log.txt`.
  `time_s` is wall-clock of the child (frames + reconstruction + export).
- Writes `out/results.json` + `out/results.md`; `--record` also writes
  `docs/benchmarks/<sha>.json` and appends rows to `docs/notes/benchmark.md`
  ("Automated benchmark (P1)" section, created once at the end of the file).
- Exit 0 only if every required video and every present optional video succeeded.
- `--out` inside `--data` is refused (data dir stays read-only).
- Tests mock `benchmark.run_video` (the child-process call), so they run in < 1 s.
