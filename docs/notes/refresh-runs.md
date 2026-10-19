# Refresh viewer runs (P3e)

The web app listed runs made before the parallax keyframes (P2b) and the sky mask + far layer
(P3b). `refresh-runs` re-runs every reference video through the current pipeline and exports
each as a NEW dated run next to the old ones. Reference: `docs/architecture.md`, "Benchmark".

## Command

```sh
cd pipeline
uv run python -m qarau_pipeline refresh-runs --data ../data/samples --runs ../data/runs \
  --suffix 2026-10-19
```

- Videos: those in `pipeline/qarau_pipeline/benchmark_set.toml` (aegviidu, mykolaiv,
  notre-dame, orono, room) with the same per-video settings (`max_frames`).
- Writes `<runs>/<video>-<suffix>` per video; `created_at` in `web/manifest.json` is the
  export time in UTC (ISO-8601), so the web app sorts the new runs first.
- Summary: a table on stdout and `refresh-<suffix>.json` (run, registered %, points,
  far-layer points, keyframes, status) in `--out` (default `--runs`; the API and the dev
  middleware list only directories with `web/manifest.json`, so it never shows as a run).
  Refused before any work if that file already exists.
- `--only notre-dame,room` limits the set; `--timeout-s` is per video (default 1800).
  Exit 1 if a video failed.

### CPU, sparse only

Per video, a child `qarau_pipeline run --video` (the benchmark's `run_video`): probe, frames
(HDR→SDR), parallax keyframes, COLMAP sparse with the sky mask, export with the far layer. No
dense stage, no `dense_remote`, no GPU, nothing paid; a test asserts the child command. Licences:
COLMAP/pycolmap (BSD-3-Clause), OpenCV (Apache-2.0), NumPy (BSD-3-Clause), Open3D (MIT), FFmpeg
as an external binary; no new library or model, VGGT not used.

### Old runs are never touched

- A video whose `<video>-<suffix>` already exists (directory, file or symlink, even dangling)
  is skipped with `skipped: run already exists`; nothing under that path is opened for writing.
- Each run is built in a hidden `<runs>/.<name>.*` temp dir (the API ignores names starting with
  a dot) and renamed into place only when it is complete. A failed or timed-out video leaves
  nothing under its name; the reason is recorded and the next video runs.
- `data/samples` entries that are not reference videos (image sets, archives, other clips) are
  listed as `skipped: not a reference video` (only without `--only`).

## Result, 2026-10-19

All 5 reference videos exported; none failed. Run on the M1 Pro CPU, 12:12–13:08 UTC (56 min).

| Video | Run | Registered % | Points | Far-layer points | Keyframes | Status |
| --- | --- | --- | --- | --- | --- | --- |
| aegviidu | aegviidu-2026-10-19 | 100.0 | 35127 | 25536 | 150 | ok |
| mykolaiv | mykolaiv-2026-10-19 | 84.8 | 5852 | 5785 | 210 | ok |
| notre-dame | notre-dame-2026-10-19 | 100.0 | 42841 | 7098 | 121 | ok |
| orono | orono-2026-10-19 | 100.0 | 77697 | 13903 | 150 | ok |
| room | room-2026-10-19 | 98.2 | 27397 | 306 | 282 | ok |
| attisholz-areal | — | — | — | — | — | skipped: not a reference video |
| ccs-corrosion | — | — | — | — | — | skipped: not a reference video |
| leipzig-wahren-depot | — | — | — | — | — | skipped: not a reference video |
| ostrava-corrosion | — | — | — | — | — | skipped: not a reference video |
| raketa-shipwreck | — | — | — | — | — | skipped: not a reference video |
| south-building | — | — | — | — | — | skipped: not a reference video |
| south-building.zip | — | — | — | — | — | skipped: not a reference video |

- Points = object cloud the viewer shows (far layer excluded); far-layer points = manifest
  `far.count` (ADR 0013); keyframes = kept frames handed to COLMAP; registered % = registered
  of those. aegviidu and orono use the benchmark cap `max_frames = 150`.
- aegviidu: the far layer is 42% of all points (drone orbit over open land: most ground and
  horizon points are far from every camera). Worth a look in the viewer with the far toggle.
- mykolaiv: an edited multi-shot night news clip, not one survey (P3c), not gated. Its model
  varies between runs: 9556 object points on 2026-10-18, 5852 today, same 84.8% registered.
- room: 98.2% registered today vs 100.0% on 2026-10-18 (5 of 282 frames dropped); COLMAP's
  incremental mapper is not deterministic across runs on the CPU.
- The image folders and the three Commons drone clips are not in `benchmark_set.toml` (a
  protected file); adding them also adds them to the merge-gate benchmark time.

Checks after the run:
- Every entry under `data/runs` (type, size, mtime, SHA-256; symlinks by target, not followed)
  listed before and after: 0 of 9717 old entries changed; the only additions are the five
  `*-2026-10-19` dirs and `refresh-2026-10-19.json`, no temp dir left.
- Through the backend (`TestClient`, no server): `GET /api/runs` lists 27 runs (22 old + 5
  new); for each new run the manifest and every file it references return 200, and
  `points.position.f32` / `far.position.f32` hold exactly `points` / `far.count` × 12 bytes.
- `created_at`: aegviidu `2026-10-19T12:32:52+00:00`, mykolaiv `12:49:09`, notre-dame
  `12:51:30`, orono `13:02:41`, room `13:08:10` (all UTC).

### Earlier refresh, 2026-10-18 (round 1 code)

| Video | Run | Registered % | Points | Far-layer points | Keyframes |
| --- | --- | --- | --- | --- | --- |
| aegviidu | aegviidu-2026-10-18 | 100.0 | 35190 | 25607 | 150 |
| mykolaiv | mykolaiv-2026-10-18 | 84.8 | 9556 | 8365 | 210 |
| notre-dame | notre-dame-2026-10-18 | 100.0 | 42896 | 7083 | 121 |
| orono | orono-2026-10-18 | 100.0 | 77707 | 13892 | 150 |
| room | room-2026-10-18 | 100.0 | 26104 | 282 | 282 |

## Viewing the runs

The viewer must serve `data/runs`: the API with its default `QARAU_RUNS_DIR=../data/runs`, or
`cd web && QARAU_RUNS_DIR=../data/runs pnpm exec vite`; then `/` lists the dated runs next to
the old ones and `/viewer/notre-dame-2026-10-19` opens one. A viewer pointed at a benchmark
output shows "manifest.json not found" for these names.
