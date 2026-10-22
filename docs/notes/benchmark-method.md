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
- `dropped_points` = points the export stage removed (non-finite + far outliers, rule in
  `docs/architecture.md`, export row); shown as `dropped N` in Notes when N > 0.

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

Later (2026-10-15, devteam gate runs): mykolaiv on byte-identical frames (all 300 extracted
frames have the same hash) gave 178/210 registered, 3 models, 10 332 points in one run and
81/210 (38.6 %), 5 models, 2 946 points in the next; aegviidu went 3 → 5 models, orono 1 → 2.
No threshold separates that from a real change, so mykolaiv is **not gated** until P3
(`devteam/config.yaml` → `gates.benchmark.per_video`, `gated: false`); it is still run and
shown, and the packet names it under "not compared". Model counts of aegviidu/orono are noisy
too: watch them when P3 lands. After P3 it stays ungated for a different reason: see
"Mykolaiv (P3c)" below.

**Mykolaiv export crash (P0, 2026-10-15).** Gate run beadd07 failed in export with laspy
`OverflowError`: the old LAS header used offset = min and a fixed 1 mm scale, which overflows
int32 beyond ~2 147 km of extent. The model itself is broken, not just noisy: 14 consecutive
registered frames (of 178) sit ~1e8 units from the other 164, and 4 748 of 10 332 points
(46 %) sit ~2.7e8 away. With that many far points the 99th-percentile rule drops nothing, so
export now succeeds (LAS scale ~2–8 cm) but the viewer bundle is useless (`reproj_check_px`
mean ~4 000 px). That is a reconstruction problem (bad registration chain), not an export
one.

**Sampling phase (P2a, 2026-10-15).** Identical runs repeat, but shifting which source frame
starts each 1/6 s slot does not. Room (60 fps) with the frame grid moved by 1–5 source frames
(17–80 ms):

| Sampler | Registered |
| --- | --- |
| old `fps` filter (a8efcc9) | 257/269 (95.5 %) ×3 |
| old `fps` filter, `start_time` +1 frame | 242/267 (90.6 %) |
| pts `select`, slot start (P2a draft) | 241/267 (90.3 %) ×2 |
| pts `select`, slot +¼, +½ | 239/267 (89.5 %), 242/268 (90.3 %) |
| pts `select` on the old grid (P2a final) | 257/269 (95.5 %) |

So a change that only moves the sampling grid can swing room by ~6 pp, and the old grid
happens to be a good phase for it. Notre-dame (30 fps drone) stays at 100 % either way. P2a
therefore keeps the old grid exactly (`frames.pts_select`); a sampler change (e.g. parallax
keyframes) should be judged on several videos, not on room alone.

## Mykolaiv (P3c, 2026-10-16)

**Verdict: stays not gated.** 1 model with ≥ 90 % registered is not reachable on this video
without a wrong model; full diagnosis: plan removed, see git history: 76d9149.

**Why: the frames.** `mykolaiv_oil_tanks.webm` is not a drone survey. It is an edited night
news clip of an oil depot fire (State Emergency Service of Ukraine): ~18 shots with hard cuts
(burning tanks, fire trucks, firefighters with hoses), title cards at frames 0–18 and 284–299,
and a broadcaster logo burned into the top-left corner of every frame. Of 300 extracted frames,
90 are dropped as too dark (night, fades to black), 210 kept: 178 frames of footage + 32 of the
title card.

**Two runs, pipeline CLI, product defaults, same code (dev at 0f867c2, uncommitted P3c docs
only), M1 Pro shared with other agents:**

| Run | Frames used | Registered | Models | Points | Reproj. px | Mapper models | Merge accepted | Reconstr. s |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 210 | 178 (84.8 %) | 2 | 13 957 | 0.739 | 3 / 178 / 17 | `mapped` (none better) | 992 |
| 2 | 210 | 178 (84.8 %) | 2 | 11 188 | 0.854 | 3 / 32 / 19 / 90 | `leftover_registration` (90 → 178, 84 bridges) | 1 150 |

**Run-to-run spread:** registered 0 pp and models 0 (both 178 / 2), but the route there differs
(mapper output 3 vs 4 models; the P3 join was needed in run 2 only); points −19.8 %, reproj
+15.6 %, export reprojection check mean 16.7 vs 2.8 px; cameras more than 20 units from the
origin 15 vs 39 (frames 231–245 vs 179–245; median distance 0.68 / 1.2). Earlier gate runs on identical frames gave
178/210 and 81/210 (see Variance). Points and reproj exceed the gate's thresholds (−10 %,
+10 %) between identical runs.

**Why the registered % is stuck at 84.8 % and why that number is not trustworthy:**

- In both runs the second model / the unregistered frames are exactly the 32 title-card frames.
  They match the footage only through fade frame 283 (dropped as dark). 178/210 is therefore
  the ceiling for any model without the title card; ≥ 90 % would need the title graphic
  registered into the fire scene.
- The main model holds **all 178 footage frames of 16 different shots**, which is not one
  scene. 2 398 (run 1) / 3 852 (run 2) of its points are seen in more than one shot; 68 % /
  61 % of their observations lie in the logo strip (x < 160 px, y < 240 px). COLMAP flags
  33 533 of 44 850 matched pairs as `WATERMARK` and ignores them, but mixed logo + scene pairs
  pass and chain the shots. Measured symptom: whole shots of cameras thrown far off (above).
- Each shot is short (0.5–11 s) from a ground camera, mostly hand-held; flames, smoke and
  water are non-rigid; most texture is dark. An honest model is one shot at a time: the
  longest has 25 kept frames (12 %).

**Tried** (all automatically in both runs, P3 merge path): dropped frames as bridges (90),
GLOMAP join over all 300 frames (43 / 32 kept frames: it does not chain unrelated shots),
relaxed leftover registration (+0 / +88). Not tried, with reason: denser sampling (the gaps are
cuts and black frames, not sampling holes), wider matching (already exhaustive), looser mapper
or merge thresholds (would register more frames through the logo: a higher % on a worse
model).

**What would make it gateable:** a continuous survey video in its place (owner decision:
data and licence; mykolaiv's source licence is not recorded in `docs/notes/`). A static-overlay
mask (no features on burned-in logos/OSD) is a real product fix for drone footage with
on-screen text, but on this clip it lowers registered %.
