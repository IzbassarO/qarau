# P2a — robust phone import (plan)

Goal: the frames stage decodes any phone video correctly — HEVC, HDR (PQ/HLG) → SDR,
rotation metadata, variable frame rate — and says what it did. Sampling stays fixed-rate
(`clamp(duration·6, 60, 300)` frames); parallax keyframes and the quality report are
separate tasks.

## Steps

1. **Probe** — new `pipeline/qarau_pipeline/probe.py`, `probe_video(path) -> VideoInfo`
   (frozen dataclass). Replaces `frames.probe()` (the dict version from 11cc97c).
   - `ffprobe -v error -print_format json -show_streams -show_format -select_streams v:0 <video>`
   - `ffprobe -v error -print_format json -select_streams v:0 -show_entries packet=pts_time -read_intervals %+#120 <video>`
   - Pure parsers take the JSON dict (`parse`, `rotation_deg`, `bit_depth`, `is_vfr`), so
     tests need no ffmpeg.
   - `rotation_deg` is clockwise 0/90/180/270: `-side_data.rotation` (ffprobe's
     displaymatrix angle is counter-clockwise; iPhone portrait = −90 → 90), else legacy
     `tags.rotate`.
   - `is_hdr`: transfer `smpte2084`/`arib-std-b67`, or primaries `bt2020` at ≥ 10 bit.
   - `is_vfr`: sorted packet pts of the first 120 packets; VFR when any interval is more
     than 25 % off the median (timebase rounding ≤ ~3 %; a dropped frame is 100 %).
2. **Decode** in `frames.py`, filter chain built from `VideoInfo`:
   - Rotation: ffmpeg autorotate (default, never a manual `transpose`). After extraction the
     first frame's orientation must match `display_width/height`, else `StageError`.
     OpenCV only reads ffmpeg-written JPEGs (no EXIF orientation), so rotation happens once.
   - HDR: `ffmpeg -filters` has `zscale` →
     `zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p`.
     Otherwise the existing `colorspace=all=bt709:iall=bt2020:itrc=bt2020-10:format=yuv420p`
     fallback (Homebrew ffmpeg has no zimg) plus a warning in the manifest.
   - Sampling by pts for every video, on the **same grid as the old `fps=R` filter**
     (round=near: per tick k the last source frame with t < (k+½)/R). `select` cannot look
     ahead, so it takes the first frame with t ≥ (k+½)/R − dt, dt = median frame interval
     (`VideoInfo.frame_dt_s`): slot = `floor((t+dt)*R-0.5)`, a frame is kept when its slot is
     above the last kept one. `-fps_mode passthrough` so the muxer neither duplicates nor
     drops; `showinfo` after it logs the **source** pts of each kept frame. (A first version
     kept the first frame in each [k/R, (k+1)/R) slot; it moved the grid and room fell to
     241/267, see Results.)
   - `frames/manifest.json`: `source` (VideoInfo), `conversions`
     (`autorotate:<deg>`, `tonemap:hable|tonemap:colorspace`, `vfr:pts_sampling`), `frames`
     (`file`, `pts_s`), `warnings`.
   - Readable errors: missing ffmpeg/ffprobe, unprobeable file, decode failure (codec named),
     zero frames.
3. **Tests** — `tests/test_probe.py` (fixture dicts: displaymatrix −90, `tags.rotate`, PQ,
   HLG, SDR, VFR tolerance, unreadable file) and `tests/test_frames.py` (lavfi `testsrc2`
   320×240 clips in `tmp_path`: H.264 control, HEVC, PQ HDR, rotation 90, 10+30 fps VFR,
   truncated file). libx265 missing → `pytest.skip` with the reason.
4. **Real data** — `IMG_8315.MOV` (iPhone HEVC HLG portrait, rotation −90 displaymatrix) and
   `notre_dame_oubiels.webm` (VP8 SDR control); look at frames.
5. **Benchmark** — quick set (`--only notre-dame room`) before and after; compare with the
   a8efcc9 rows in `docs/notes/benchmark.md`.
6. **Docs** — `docs/architecture.md` frames row.

## Results (2026-10-15, M1 Pro, uncommitted tree)

Quick benchmark, before = HEAD 5df0e9a, after = this change:

| Video | Commit | Frames used | Registered % | Models | Points | Reproj. px | Time s |
| --- | --- | --- | --- | --- | --- | --- | --- |
| notre-dame | a8efcc9+dirty (notes) | 109 | 100.0 | 1 | 43264 | 0.395 | 184.4 |
| notre-dame | 5df0e9a (before) | 109 | 100.0 | 1 | 43300 | 0.395 | 110.3 |
| notre-dame | P2a v1, slot-start sampler | 108 | 100.0 | 1 | 43192 | 0.397 | 105.9 |
| notre-dame | **P2a final, old-grid sampler** | 109 | 100.0 | 1 | 43361 | 0.395 | 111.5 |
| room | a8efcc9+dirty (notes) | 269 | 95.5 | 2 | 26706 | 0.93 | 464.9 |
| room | 5df0e9a (before) | 269 | 95.5 | 2 | 26503 | 0.933 | 294.9 |
| room | P2a v1, slot-start sampler (×2) | 267 | 90.3 | 3–4 | 24784 / 24863 | 0.939 / 0.936 | 237.5 / 242.7 |
| room | **P2a final, old-grid sampler** | 269 | 95.5 | 3 | 26699 | 0.93 | 260.5 |

The first sampler (first frame in each [k/R, (k+1)/R) slot) moved the grid by a few source
frames and room fell 5 pp; so did the old `fps` filter shifted by one frame (242/267,
`benchmark-method.md`, "Sampling phase"). The final sampler reproduces the old grid, and room is
back at 257/269. Its largest model matches the old runs (registered, points, reproj); COLMAP
also built a third, minor model.

Frames picked vs. the old `fps` filter (ffmpeg `showinfo` checksums, full decode):

| Video | Same frames |
| --- | --- |
| room (IMG_8315.MOV, 60 fps, timebase 1/600) | 283/283, pixel-identical |
| mykolaiv (webm) | 300/300 |
| orono (webm, max 150) | 150/150 |
| aegviidu (ogv, max 150) | 150/150 by source index; 4–8 frames per run differ in pixels in **both** samplers (theora decode under filter-graph threading is not bit-exact) |
| notre-dame (webm) | 119/121: two frames sit exactly on a boundary with ms-rounded pts (e.g. B = 10.1 s, frame at 10.1 s) and go one frame the other way |

`IMG_8315.MOV`: probe → hevc 3840×2160, `rotation_deg` 90 → display 2160×3840, HLG BT.2020
10-bit, `is_hdr`, 59.97 fps, not VFR. Manifest: `["autorotate:90", "tonemap:colorspace"]` plus
the no-zscale warning (Homebrew ffmpeg 9.0.2 has no libzimg), 283 frames 1080×1920, pts
0.0833 … 47.153 s, strictly increasing. Frames 0/140/200/282 inspected: upright, natural
colours, not grey. `notre_dame_oubiels.webm`: vp8 640×480, conversions `[]`, warnings `[]`,
121 frames, pts 0.067 … 20.1 s.

zscale/hable: not runnable on this Mac (Homebrew ffmpeg has no libzimg; docker needs an
approval this session cannot get). CI installs Ubuntu's apt ffmpeg, which has libzimg and
libx265, so there `test_hdr_pq_to_sdr` must take the hable path (it asserts
`conversions == ["tonemap:hable"]` and no warnings) and `test_hable_chain_runs` runs
`HDR_HABLE` on a PQ-tagged 10-bit lavfi source; an invalid chain fails both loudly. Locally
`test_hable_chain_runs` skips with the reason.

Licences: FFmpeg/ffprobe LGPL-2.1+ (GPL with x265/zimg) called as an external CLI, not
linked; on-prem images that ship an ffmpeg binary must carry its licence. libx265 (GPL-2.0)
only generates test clips. zimg WTFPL, OpenCV Apache-2.0, NumPy BSD. No new dependencies.
