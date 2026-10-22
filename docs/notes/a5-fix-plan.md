# A5-fix — far-layer e2e without local data

`web/e2e/fixtures/far-layer/makeFarBundle.ts` generates 300 object points in a unit
cube, 200 far points on a shell, a version-1 manifest and an SVG frame in memory.
Arrays use little-endian XYZ float32, RGB uint8 and first-seen/observation uint16.
Exact `page.route` handlers use production `runUrl`, preserving `/files/web/` paths.
The Vite viewer uses the existing `RequireAuth allowInDev` route.

Route interception keeps the fixture in memory and uses the real viewer fetch and
parser paths without depending on Vite's local runs directory or backend data.
The malformed test uses the builder's `brokenFar` option to truncate far positions
to four bytes and checks the warning, 300 rendered object points and absent far
toggle. A valid control checks the far count and toggle. Real-data skips are
unchanged; no local data or binary blobs are required.

`web/src/lib/farFixture.test.ts` imports the same builder and tests the actual
`loadManifest` and `loadBundle` parsers through an in-memory fetch mock. The intact
fixture must load 300 object points and 200 far points. The exact broken variant
used by e2e must raise the recoverable `FarLayerError` with `farError: 'broken'`,
drop the far layer and preserve the object's positions, colours and assembly data.

No dependencies added. Installed package licences checked: Playwright/TypeScript
are Apache-2.0; React/Three.js/Vitest are MIT, all permitting commercial use.

Verification for this revision: `pnpm lint`, `pnpm typecheck`, `pnpm test` (501 tests
in 29 files) and `pnpm build` passed from `web/`. Vite reports the application's
chunk exceeding 500 kB; no production modules or build settings changed.
Playwright and the `/viewer/demo` visual check remain for the tester's e2e gate.
No Playwright runs, screenshots or servers were started in this revision.
