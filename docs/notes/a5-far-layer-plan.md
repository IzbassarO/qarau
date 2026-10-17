# A5 — optional far-point layer

1. Follow ADR 0003 and `pipeline/qarau_pipeline/export.py`: the additive manifest key is
   `far: { count, position, color, rule }`. `web/far.position.f32` is little-endian float32
   XYZ (3 values / 12 bytes per point); `web/far.color.u8` is uint8 RGB (3 bytes per point).
   Both use the object's world frame. Far points have no tracks or first-seen array.
2. Load far positions and source RGB into their own typed arrays. Preserve old bundles.
   Reject malformed declared layers with a typed, recoverable error carrying the loaded
   object bundle; both viewer entry points recover it and display a localized warning.
3. Render a separate `THREE.Points`, flat gray from `--color-notseen` (fallback `#6B727D`),
   hidden by the Quick Scan default. Keep framing, picking and navigation on object data.
   Dispose far geometry and material on unload.
4. Add a localized Show-panel toggle (RU/KK/EN), hidden without usable far points, and a
   sparse synthetic ring in the demo fixture using the same manifest and array format.
5. Cover loading, malformed-layer recovery, defaults, rendering/isolation, disposal,
   unchanged bounds and UI interaction. Run lint, typecheck, unit tests, build and
   Playwright; check demo and a real Notre-Dame bundle without taking screenshots.
   Keep all generated artifacts in the assigned A5 scratch directory and stop servers.

No new dependencies or models. Installed package metadata confirms commercial-use
licences: Three.js, React, Vitest and Testing Library are MIT; Playwright is Apache-2.0.
Existing CSS already defines `--color-notseen: #6b727d`.

## Verification

- `cd web && pnpm lint && pnpm typecheck` — passed.
- `cd web && pnpm test && pnpm build` — passed again after tester feedback: 20 files,
  328 tests. Vite reports
  the existing large-chunk advisory; the build succeeds.
- An additional scratch-only Vitest check loaded the actual P3b quick-benchmark bundle
  at `.state/bench-dev/P3b/notre-dame`: 36,450 object points and 6,921 far points. It
  verified separate arrays, unchanged object/track data, gray hidden far geometry,
  toggling and unchanged framing/navigation with the real data (WebGL renderer mocked).
- Playwright was attempted with that benchmark as `QARAU_RUNS_DIR` and artifacts under
  the assigned A5 scratch directory. This sandbox denies local listeners (`EPERM` for
  both IPv6 and IPv4), so Vite cannot start. A standalone browser launch also fails with
  macOS Mach-port permission denial. Browser/page checks must run in the tester's
  environment; no browser success or visual verification is claimed here.
- Added Playwright coverage for demo/Notre-Dame far visibility and malformed-array
  recovery. The unrelated changes to `e2e/viewer.spec.ts` were reverted after tester
  feedback; it is byte-for-byte identical to the version before A5.
- No server was left running. No pipeline or data files were changed.

## Tester follow-up: which Notre-Dame is served

The dev_app screenshot command in `devteam/config.yaml` sets
`QARAU_RUNS_DIR={data}/runs`. Its `notre-dame/web/manifest.json` contains **15,239 object
points and no `far` key**. Hiding the far toggle for that legacy run is expected. The
toggle's screenshot criterion applies to `/viewer/demo`, which includes synthetic far
arrays and starts unchecked; the old Notre-Dame must still open without an error banner.

The real P3b run is a different directory:
`/Users/izbassar/Documents/Startup/qarau/devteam/.state/bench-dev/P3b/notre-dame`.
Its manifest has **36,450 object points and 6,921 far points**, with exactly the key and
paths written by `export.py`: `far.position = "web/far.position.f32"` (83,052 bytes,
6,921 × 12) and `far.color = "web/far.color.u8"` (20,763 bytes, 6,921 × 3).

Two scratch-only integration tests now exercise the actual dev-runs middleware, real
`loadManifest`/`loadBundle`, `ViewerPage` and engine for both directories. Both passed:
the legacy run has no toggle; P3b renders 6,921 gray far points separately and shows an
unchecked **«Дальние точки»** toggle that turns the layer on. Only WebGL is mocked;
these are not browser or screenshot results. A permanent regression test also covers
the complete served-manifest → raw arrays → Russian toggle path with a synthetic P3b
contract, without mocking the loader. The Playwright test now inspects the manifest
response received by the page and attaches its URL, point count and far declaration.

Playwright was retried on P3b with the far-layer, navigation, login and control-center
specs (no screenshots), using `--reporter=line,json`. The JSON report is at
`/Users/izbassar/Documents/Startup/qarau/devteam/.state/scratch/A5/playwright-results.json`.
It records a Vite bootstrap failure (`listen EPERM ::1:5195`), **zero executed tests**.
Browser acceptance remains unverified in this sandbox. To run just the feature check
where local listeners and Chromium are allowed:

```sh
cd web
QARAU_RUNS_DIR=/Users/izbassar/Documents/Startup/qarau/devteam/.state/bench-dev/P3b \
  pnpm e2e e2e/far-layer.spec.ts \
  --output=/Users/izbassar/Documents/Startup/qarau/devteam/.state/scratch/A5/playwright
```
