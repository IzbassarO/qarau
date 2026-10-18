# A5-fix — far-layer e2e without local data

`web/e2e/fixtures/far-layer/makeFarBundle.ts` generates 300 object points in a unit
cube, 200 far points on a shell, a version-1 manifest and an SVG frame in memory.
Arrays use little-endian XYZ float32, RGB uint8 and first-seen/observation uint16.
Exact `page.route` handlers use production `runUrl`, preserving `/files/web/` paths.
The Vite viewer uses the existing `RequireAuth allowInDev` route.

The malformed test truncates far positions to four bytes and checks the warning,
300 rendered object points and absent far toggle. A valid control checks the far
count and toggle. Real-data skips are unchanged; no local data or binary blobs are
required. Extra password/thumbnail assertions and unrelated npm scripts were removed.
No dependencies added: Playwright/TypeScript are Apache-2.0; Three.js/Vitest are MIT.

Verification: lint, typecheck, 499 Vitest tests and build passed. Playwright attempts
with an empty `QARAU_RUNS_DIR` fail before tests at Vite startup (`listen EPERM` on
both IPv4 and IPv6); a browser pass remains unverified. Latest logs are in assigned
scratch: `lint-typecheck-revision3.log`, `test-build-revision3.log` and
`playwright-revision3.log`.

Completion requires the orchestrator to run `pnpm exec playwright test
e2e/far-layer.spec.ts --reporter=list` outside this sandbox with an empty runs
directory. Its check list and CI workflow are protected and currently omit Playwright.
No servers are left running.
