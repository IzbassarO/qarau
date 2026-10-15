# Benchmark method (P1)

```
cd pipeline
uv run python -m qarau_pipeline benchmark --data ../../data/samples --out /tmp/qarau-bench \
    [--only notre-dame orono] [--timeout-s 1800] [--record]
```

**Manifest.** `pipeline/qarau_pipeline/benchmark_set.toml`: one `[[video]]` per reference video
(`name`, `file`, `required`, `notes`). Required: the 4 public drone videos. Optional: the
owner's room video `IMG_8315.MOV` (private, absent from the merge gate's data dir). Image-folder
datasets (south-building, …) are not in the set yet.

**Run.** Each video runs `python -m qarau_pipeline run --video …` (frames → reconstruction →
export, default parameters) in a child process with a per-video timeout, output and log in
`<out>/<name>/` (`log.txt`, `run.json`, `colmap/`, `web/`). Nothing is written into `--data`;
`--out` inside `--data` is refused. Metrics come from the reconstruction stage in `run.json`.

**results.json** (`<out>/results.json`, plus a markdown copy `<out>/results.md`):

```json
{"commit": "667d6ee", "dirty": false, "pipeline_version": null, "started_at": "2026-10-14T…",
 "videos": {"notre-dame": {"registered_pct": 100.0, "models": 1, "reproj_px": 0.4,
   "points": 43317, "scale_err_cm": null, "time_s": 170.2, "frames_used": 109, "registered": 109}},
 "errors": {"orono": "timeout after 1800 s"}, "skipped": ["room"]}
```

- `frames_used` = kept frames given to COLMAP; `registered` / `points` / `reproj_px` = the
  largest model (most registered images); `models` = all models COLMAP built.
- `registered_pct` = 100 · registered / frames_used.
- `scale_err_cm` = `null` until a scale reference exists (P5/P7).
- `time_s` = wall-clock of the whole child run, export included.

**Exit code.** 0 only if every required video and every present optional video ran. A missing
required video, a failure or a timeout → listed under `errors`, the other videos still run,
exit 1. A missing optional video → `skipped`, not an error.

**--record** (manual runs only; the merge gate never passes it) also writes
`docs/benchmarks/<short sha>.json` (copy of results.json) and appends one row per video to the
"Automated benchmark (P1)" section at the end of `docs/notes/benchmark.md` (a failed video gets
a `failed: <reason>` row, so it is never silently missing). The commit comes
from `git rev-parse --short HEAD`; uncommitted changes show as `<sha>+dirty` in the row and
`"dirty": true` in the JSON.

**Variance.** COLMAP is not deterministic (multi-threaded matching and mapping). Two runs of
notre-dame on the same code, 2026-10-14: 109/109 registered, 1 model, 0.395 px both times;
points 43 317 vs 43 269 (−0.1 %); time 141 s vs 149 s (+6 %, machine load). The gate's
thresholds (registered −2 pp, reproj +10 %, points −10 %) sit well above that noise; time is
not gated.

**Run time.** On an M1 Pro the two high-resolution drone videos (Aegviidu 1920×1080, Orono
2560×1440) each spend > 28 min in exhaustive matching of 300 frames and hit the 1800 s
timeout, so the full set does not fit the gate's 90-min budget yet (see `progress.md`).
