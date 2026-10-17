# A6b — point info

## Bundle findings

- `Bundle.position` contains aligned XYZ float32 model coordinates. The exporter rotates
  up to +Z and centres the cloud, explicitly **without scale**.
- `pointOffsets` / `pointFrames` are point-major CSR observation tracks; frame links use
  the frame array index (`i` in current exports). `frameOffsets` / `frameInPoints`
  contain the reverse links; use valid supplied links even without a manifest declaration.
- `manifest.frames` supplies `i`, timestamp `t`, `thumb`, and registered-camera
  `pose.qvec` (COLMAP [w,x,y,z], camera-from-world), `pose.tvec`, `camera.K`
  ([fx,fy,cx,cy]), `camera.params`, model and image width/height.
- Current exports contain neither metric scale nor per-point confidence. Finding
  confidence, observation counts and reprojection error are not point confidence.
- The existing engine `pickAt` / `pickNearest` returns a point index and `setPicked`
  already provides a selection marker. Navigation has a separate ray-based orbit pivot.
- Playback uses extracted source frames, with `jump` selecting a frame and its camera.

## Implementation

1. Add a pure, tested point-info builder: prefer actual CSR tracks (including empty
   tracks); otherwise project through registered cameras, respecting supported radial
   distortion, and mark visibility estimated because occlusion is unchecked. Preserve
   frame timestamps; missing timestamps disable seeking, without an inferred FPS.
2. Define optional web-side metadata hooks `world.scale_m_per_unit` and
   `points.confidence` (float32 file loaded as `pointConfidence`). No current bundle is
   assigned either. Accept only finite positive scale and finite confidence in [0,1].
   Scaled height and distance reference the model origin, not an invented ground datum;
   height follows declared world-up. Coordinates remain model units unless scaled.
3. Add an i toolbar tool with localized accessible labels, reusing engine picking and
   its marker. Only clicks without movement select; orbit, pan, fly and alignment retain
   their controls. Do not add shortcuts. Add a panel before the video frame in the
   existing sidebar, authenticated thumbnails, exact-timestamp seeking, close and empty
   states, and clear unavailable rows for scale (P5a) / confidence (P6).
4. Parse `?point=<index>` on the viewer route. Valid indices open the tool/panel;
   malformed and out-of-range values show a localized notice without an error banner.
5. Add RU/KK/EN keys, pure data/panel tests, route/picking regressions and a browser
   check for `/viewer/notre-dame?point=0`. Run lint, typecheck, all Vitest tests, build
   and the configured Playwright suite. Browser checks capture no screenshots; the
   tester owns screenshots. Put all generated test artifacts in the task scratch folder
   and stop each server immediately after its check.

## Scope and licences

Only `web/` and this note change; no pipeline, data, dependency or lockfile changes.
Installed package metadata confirms React, Three.js, Vitest and Testing Library use
MIT; Playwright uses Apache-2.0. All permit commercial use. The existing i18n module is
project code, with no additional library. No models or new dependencies are used.

## Verification

- `pnpm lint`, `pnpm typecheck`, `pnpm test` and `pnpm build` pass. Translation-key
  parity, both visibility paths, absent timestamps, unavailable measurements, exact
  thumbnail seeking, point picking and valid/invalid deep links are covered.
- Added screenshot-free Playwright scenarios for `/viewer/notre-dame` and
  `/viewer/notre-dame?point=0`, including evidence thumbnails, selection and drag.
  Attempted the configured suite excluding the existing screenshot-taking scenarios
  (`--grep-invert 'viewer:|missing or broken findings file'`). Vite could not start:
  this sandbox denies listening on both `::1:5199` and `127.0.0.1:5199` with `EPERM`.
  Browser verification and tester screenshots therefore remain external checks.
- Build output and Playwright artifacts are in the A6b scratch folder. No screenshots
  were taken. No server started successfully; port 5199 has no listener.
