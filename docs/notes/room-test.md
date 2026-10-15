# Room test — owner's iPhone video, 2026-10-14

Input: `data/samples/IMG_8315.MOV` (the owner's room video; the brief called it `room.mov`).
iPhone 17, 47.3 s, 3840×2160 HEVC 10-bit, **HDR HLG / BT.2020**, **rotation −90°** (portrait),
variable frame rate (~60 fps). Used as recorded; nothing substituted.

## Results

| Run | Frames used | Registered (largest model) | Models | Points | Reproj. error | Time |
| --- | --- | --- | --- | --- | --- | --- |
| M1 defaults (`room-m1`) | 111 of 118 | **14** | 4 | 869 | 0.98 px | 2.0 min |
| After fixes (`room`) | 269 of 283 | **244** | 3 | 25 997 | 0.93 px | 5.4 min |

Experiments on the same video at 6 fps (269 frames), scratch script, before changing the product:

| Matching | Mapper | Registered (largest) | Models | Points | Reproj. |
| --- | --- | --- | --- | --- | --- |
| sequential, overlap 10 | incremental | 136 | 8 | 12 744 | 0.85 |
| sequential, overlap 10 | global | 242 | 3 | 725 | 1.16 |
| sequential + loop detection | incremental | 215 | 4 | 20 879 | 0.88 |
| exhaustive | incremental | 257 | 2 | 26 688 | 0.93 |

Global mapper registers frames but triangulates almost nothing (725 points): not used.
Incremental mapping is not deterministic: same settings gave 257 in the experiment, 244 in the
product run.

## What was changed in the product pipeline (commit on `feat/room-test`)

1. **HDR → SDR.** iPhone records HLG BT.2020; read as plain BT.709 the frames came out washed
   out. HDR input now goes through ffmpeg `colorspace` to BT.709 (approximation: HLG curve
   read as BT.2020 gamma, highlights clip; proper `zscale`+`tonemap` needs libzimg, missing in
   Homebrew ffmpeg).
2. **Rotation.** ffmpeg already auto-rotated pixels; `probe` now reports the displayed size
   (2160×3840) and the rotation, and the 1920 px cap applies to the long side (portrait
   frames were 1920×3413 before).
3. **Frame rate 2.5 → 6 fps** (still capped at 300 frames). Hand-held pans lose overlap at
   2.5 fps. Videos over 120 s were already at the cap and do not change; shorter videos get
   up to 2.4× more frames (more processing time).
4. **Exhaustive matching up to 300 frames**, sequential + loop detection above. Sequential
   matching splits videos that come back to the same place.

## Still not good

- Frames 0–32 (first 5.5 s: shelves, blank wall, clothes on the door) are not on the map:
  plain wall, fast pan. 6 more single frames missing (blur).
- The map is **sparse** (26 k points). Walls are almost empty: white paint has no features
  for SIFT. Needs dense reconstruction (Phase 1.4).
- **A4 check not possible.** No clean A4 sheet is visible in the video. On the dresser there
  is a tan sheet under cables, cards, a jar and a glasses case; its corners are hidden. The
  desk in the next room is seen only from far at a grazing angle.
- Scale is unknown (relative units). Camera height above the fitted floor plane is 7.2–8.0 u
  while filming the bed/dresser and 3.2–4.8 u near the door and bags: either the phone
  was lowered there or the model drifts. Cannot tell without a reference object.
- Bed (green points) footprint 8.9 × 6.0 u, ratio 1.49. A twin mattress is 1.97, but the
  duvet hangs over the sides; not a usable check.

## To re-shoot for the scale check

A4 flat on a clear surface, nothing on it, all four corners visible; film it from 3–4
directions at 0.5–1 m while walking (not turning on the spot), then continue the room.

## Screenshots

- `room-test/01-height.png` — height colours (default look).
- `room-test/02-rgb.png` — same view, "Исходные цвета" on: bed, floor, rug, bags readable.
- `room-test/03-top-rgb.png` — top view, real colours, camera path.
