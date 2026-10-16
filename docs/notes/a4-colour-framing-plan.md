# A4 — Colour toggle and object framing

1. Make bundle RGB optional and validate its length; absent colours never become fabricated RGB.
2. Keep height and RGB attributes in the viewer, expose `colorMode` / `setColorMode`, and retain shader priority for findings and never-seen coverage.
3. Add localized colour controls and unavailable-RGB tooltip, with a localStorage preference shared across this browser user's viewer session (authentication is not implemented yet).
4. Use point-only percentile bounds and an aspect-aware frustum fit for initial/reset views; retain all point and registered-camera positions for navigation limits and collision handling.
5. Add geometry, engine, bundle and page tests; run lint/Prettier, TypeScript, Vitest and build. Open `/viewer/demo` and `/` with the local browser without screenshots.

No dependencies or models are added. Existing Three.js, React, Vite, Vitest, Testing Library and Playwright licences are MIT or Apache-2.0 and permit commercial use (checked in installed package metadata/licences).

Only `web/` and `docs/` are edited. No progress-file edits, commits, branches, pushes or screenshots.

## Verification

- `cd web && pnpm lint && pnpm typecheck` — passed (ESLint, Prettier and TypeScript).
- `cd web && pnpm test && pnpm build` — passed: 14 test files, 248 tests, including
  geometry outliers/frustum fits, colour-buffer swaps/finding colours, optional RGB
  loading, viewer controls/persistence and matching EN/RU/KK keys. Production build
  succeeds with the existing large-chunk advisory.
- Additional review caught and fixed corner cropping when navigation limits shorten
  camera distance. Tests now cover cube, flat, tall, wide and tiny boxes.
- Browser verification remains unavailable in this sandbox: starting Vite on
  `127.0.0.1:5199` failed with `listen EPERM`. Consequently `/viewer/demo` and `/` have
  not been visually checked here. No screenshots were attempted; the external tester
  still needs to check these pages with a running dev server.
