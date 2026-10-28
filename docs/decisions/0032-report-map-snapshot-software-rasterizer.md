# 0032. Report map snapshot: a NumPy point rasterizer from a fixed camera, markers as SVG

Date: 2026-10-27. Status: accepted (RPT1 part 2). Extends ADR 0027.

## Context

The PDF report (ADR 0027) shows the 3D map once, with every finding on it, and each finding card
shows where on the map the finding is and its 3 evidence frames with the region outlined. The
report's SHA-256 is stored (RPT1 part 3), so the same inputs must give the same PDF bytes, on a
laptop, in GitHub CI and in the API/worker container on a customer's server. None of those has a
GPU, CI has no display, and the API renders the report inside a request.

Options for the map picture:

- **Potree / the web viewer in a headless browser.** Needs Chromium + WebGL (SwiftShader) in the
  API image, a running page and a screenshot; the pixels depend on the browser and GPU driver
  version, and a request would start a browser. Too heavy and not reproducible.
- **Open3D offscreen (`OffscreenRenderer`, Filament).** Needs EGL or OSMesa: not on macOS, and in
  CI only with extra system packages; output depends on the GL driver; Open3D is a ~400 MB
  dependency the API does not have today.
- **A software rasterizer in NumPy, PNG via Pillow.** Orthographic projection, a z-buffer and a
  square per point are ~40 lines of NumPy. No GL, no display, already-installed dependencies
  (NumPy BSD-3-Clause, Pillow MIT-CMU), and the same input gives the same pixels everywhere.

## Decision

`pipeline/qarau_pipeline/report/snapshot.py`:

- **What is drawn.** The viewer bundle of the inspection's last exported run (the run findings
  are marked on, as the API's `exported_run` picks it): its dense layer when it has one (what
  the viewer shows), else its sparse points, with their colours, on the v8 `map-ground`.
- **Fixed camera.** Orthographic, 30° above the horizon, looking from the side the video was
  filmed from: the azimuth is the mean of the registered cameras' horizontal directions from
  the cloud's centre (the bundle origin, ADR 0003). When the cameras stand all around the
  object (mean shorter than 0.1) or there are none, it looks from -y. The camera is a rule over
  the bundle, not a free parameter, so a report of the same bundle always shows the same view.
- **Framing.** The 0.5–99.5 % range of the projected points (nearest point, so a small cloud is
  framed by its extremes; stray points past it are cut off) fills the 1300 x 580 px image (the
  v8 650 x 290 px report figure at 2x) less 24 px on every side.
- **Rasterizer.** Every point is a 3 x 3 px square; where squares overlap, the point nearest
  the camera wins, ties by the lower point index (`np.lexsort` per pixel, not "last write wins",
  so the order of points never changes the picture). PNG without metadata.
- **Markers are not in the PNG.** The template draws each not-rejected finding as a numbered
  SVG circle over the image (severity colour, white figures, the report's embedded Plex font) at
  the projected centre of the finding's points. Rejected findings are not drawn, as in the v8
  reference report. Each card shows a 480 x 240 px crop of the map around its marker with the
  same marker, and says "number N on the map in section 2".
- **Evidence frames.** A finding's first 3 observations in the API's order (DEF1-api picks and
  orders them) are embedded as the frame image with an inline SVG whose viewBox is the frame's
  pixel size (the camera's width and height from the manifest), so the stored outline
  `[[u, v], ...]` becomes `<polygon points="u,v ...">` unchanged. A missing image file is said
  in plain words on the plate; there is no silent blank.
- Images go into the HTML as data URIs: the PDF does not depend on where the runs dir is
  mounted, only on the file contents.
- `--runs-dir` (default `QARAU_RUNS_DIR`) tells the CLI where run dirs live; without it the
  report says "No map" and every plate says the image was not found.

## Consequences

- Deterministic and headless: `tests/test_report_snapshot.py` checks literal pixel colours of a
  synthetic cloud (top-down and side views, z-buffer order), byte-identical PNGs, the marker and
  crop of a fixture run, and 3 images with literal polygon strings per card.
- A point map is not a shaded surface: a sparse cloud looks sparse (notre-dame-p3e: 42 k points
  in a drone orbit read as a speckled landscape). Dense bundles look much better.
- Cost (M1 Pro, 2026-10-27): notre-dame-p3e (42 748 sparse points) 0.2 s, 283 KB of data
  URI; notre-dame-p3e-modal (730 069 dense points) 1.9 s, 733 KB. Frames are embedded as stored
  (≤ 1920 px long side, frames stage); the notre-dame report with 2 findings is 0.9 MB, and a
  report grows by 3 frames per finding.
- Not done (later, when asked): a scale bar, `not_seen` regions in gray (COV1), several runs per
  inspection on one map, a free camera chosen by the engineer.
