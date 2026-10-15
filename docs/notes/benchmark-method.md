# Benchmark method (P1)

```
cd pipeline
uv run python -m qarau_pipeline benchmark --data ../../data/samples --out /tmp/qarau-bench \
    [--only notre-dame orono] [--timeout-s 1800] [--record]
```

**Manifest.** `pipeline/qarau_pipeline/benchmark_set.toml`: one `[[video]]` per reference video
(`name`, `file`, `required`, `notes`, optional `max_frames`). Required: the 4 public drone
videos. Optional: the owner's room video `IMG_8315.MOV` (private, absent from the merge gate's
data dir). Image-folder datasets (south-building, …) are not in the set yet.

**Profile (`max_frames`).** Aegviidu (1920×1080) and Orono (2560×1440) run with
`--max-frames 150` instead of the default 300 frames. At 300 frames each one spent more than
28 min in exhaustive matching on an M1 Pro and hit the 1800 s timeout, so the set could not
fit the gate's 90-min budget. Why a frame cap and not sequential matching: with ≤ 300 frames
the product uses exhaustive matching, and a cap keeps exactly that code path (frames,
features, matching, mapping, export), only with fewer frames. Switching the benchmark to
sequential matching would leave the product's exhaustive path unmeasured, so its regressions
would be hidden. The cap is stored per video (`max_frames` in results.json, "Notes" column).
Changing it starts a new baseline for that video; do it only in its own PR. The other videos
run on product defaults.

**Run.** Each video runs `python -m qarau_pipeline run --video … [--max-frames N]` (frames →
reconstruction → export, otherwise default parameters) in a child process with a per-video
timeout (`--timeout-s`, default 1800 s: the whole required set takes ~40 min, so one video
hanging still leaves room inside the gate's 90 min), output and log in
`<out>/<name>/` (`log.txt`, `run.json`, `colmap/`, `web/`). Nothing is written into `--data`;
`--out` inside `--data` is refused. Metrics come from the reconstruction stage in `run.json`.

**results.json** (`<out>/results.json`, plus a markdown copy `<out>/results.md`):

```json
{"commit": "667d6ee", "dirty": false, "pipeline_version": "0.1.0", "started_at": "2026-10-14T…",
 "videos": {"notre-dame": {"registered_pct": 100.0, "models": 1, "reproj_px": 0.4,
   "points": 43317, "scale_err_cm": null, "time_s": 170.2, "frames_used": 109, "registered": 109,
   "max_frames": null}},
 "errors": {"orono": "timeout after 1800 s"}, "skipped": ["room"]}
```

- `pipeline_version` = installed package version, else `version` from `pipeline/pyproject.toml`
  (the uv project is run from source, not installed).

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
"Automated benchmark (P1)" section at the end of `docs/notes/benchmark.md`. A failed video gets
a row with `—` in the metric columns and `FAILED: <reason>` in Notes, so it never goes missing
silently. The commit comes from `git rev-parse --short HEAD`, and uncommitted changes show as
`<sha>+dirty` in the row and `"dirty": true` in the JSON. To get a clean row, commit first and
then run `--record`.

**Run time.** Recorded run on a8efcc9+dirty, M1 Pro shared with other agents: the 4 required
videos took 62 min (aegviidu 22 min, orono 21, mykolaiv 16, notre-dame 3), room +8 min, all
exit 0. The gate allows 90 min. On an idle machine it is about a third faster (mykolaiv 645 s
vs 984 s).

**Variance.** COLMAP is not deterministic (multi-threaded matching and mapping). Same
frames/reconstruction code, three runs on 2026-10-14/15:

| Video | Registered | Points | Reproj. px |
| --- | --- | --- | --- |
| notre-dame | 109/109 ×3 | 43 317 / 43 269 / 43 264 (≤ 0.1 %) | 0.395 ×3 |
| mykolaiv | 178/210 ×2 | 10 845 / 13 384 (**+23 %**) | 0.638 / 0.739 (**+16 %**) |
| room | 257/269 ×2 | 26 688 / 26 706 | 0.931 / 0.930 |

Notre-dame and room sit well inside the gate's thresholds (registered −2 pp, reproj +10 %,
points −10 %). **Mykolaiv does not**: it splits into 3 models, and the points and reproj of
the largest one swing more than the thresholds between identical runs. So the gate can fail or
pass mykolaiv by chance. Fix options for the gate owner: per-video tolerances, the median of
2–3 runs, or gating mykolaiv on registered % only. Time is not gated.
