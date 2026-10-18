# P3e: refresh viewer runs (2026-10-18)

The web app still listed runs made before the parallax keyframes (P2b) and the sky mask + far
layer (P3b). Every reference video was re-run through the current pipeline and exported as a
NEW run next to the old ones. Usage: `docs/architecture.md`, "Benchmark".

## Command

```sh
cd pipeline
uv run python -m qarau_pipeline refresh --data ../data/samples --runs ../data/runs --suffix 2026-10-18
```

Sparse only, on the CPU (M1 Pro): `run` per video in a child process, per-video `max_frames`
from `benchmark_set.toml`. No dense stage, no `dense_remote`, no GPU, nothing paid. Licences:
pycolmap/COLMAP (BSD-3-Clause), OpenCV (Apache-2.0), NumPy (BSD-3-Clause), FFmpeg as an external
binary; no new library, VGGT not used.

## Result

All 5 videos of `benchmark_set.toml` exported; none failed.

| Video | Run | Registered % | Points | Far-layer points | Keyframes | Status |
| --- | --- | --- | --- | --- | --- | --- |
| aegviidu | aegviidu-2026-10-18 | 100.0 | 35190 | 25607 | 150 | ok |
| mykolaiv | mykolaiv-2026-10-18 | 84.8 | 9556 | 8365 | 210 | ok |
| notre-dame | notre-dame-2026-10-18 | 100.0 | 42896 | 7083 | 121 | ok |
| orono | orono-2026-10-18 | 100.0 | 77707 | 13892 | 150 | ok |
| room | room-2026-10-18 | 100.0 | 26104 | 282 | 282 | ok |

- Points = object cloud the viewer shows (far layer excluded); far-layer points = manifest
  `far.count` (ADR 0013); keyframes = kept frames handed to COLMAP; registered % = registered
  of those.
- aegviidu and orono use the benchmark cap `max_frames = 150`, so their keyframes are the cap.
- aegviidu: the far layer is large (42% of all points): a drone orbit over open land, most
  ground/horizon points are far from every camera. Worth a look in the viewer (A5 far toggle).
- mykolaiv: an edited multi-shot night news clip, not one survey (P3c); 84.8% registered is
  expected and it is not gated.
- `created_at` (manifest) is the export time with timezone, e.g.
  `2026-10-18T20:15:35+00:00` for notre-dame.

## Skipped

`data/samples` entries not in `benchmark_set.toml` (refresh runs that set only):

| Entry | Reason |
| --- | --- |
| attisholz-areal | folder with a Commons drone video (defect-hunting clip, `docs/notes/datasets.md`); not in the benchmark set |
| leipzig-wahren-depot | folder with a Commons FPV drone video (same); not in the benchmark set |
| raketa-shipwreck | folder with a Commons drone orbit video (same; old runs `raketa`, `raketa-seg`); not in the benchmark set |
| ccs-corrosion | segmentation dataset (zip of photos + masks), no video |
| ostrava-corrosion | segmentation dataset (zip of photos), no video |
| south-building | COLMAP image set, no video; the benchmark runs the video path only |
| south-building.zip | archive of south-building |

The three video folders can be refreshed once they are added to `benchmark_set.toml` (a
protected file, so a separate decision: it also adds them to the merge-gate benchmark time).

## Old runs untouched

Before the refresh and after it, every entry under `data/runs` was listed with its type, size,
mtime and SHA-256 (symlinks: their target), without following symlinks. All 6095 old entries
were identical afterwards; the only additions were the five `*-2026-10-18` directories. The
temp dirs (`data/runs/.<run>.*`) were renamed into place, so none was left. `GET /api/runs`
lists 22 runs: the 5 new ones next to the 17 old ones.
