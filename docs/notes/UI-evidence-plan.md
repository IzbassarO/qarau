# UI-evidence — part 1 of 2: trim branch and data layer

The owner narrowed this round to data helpers and removal of fabricated evidence and decision code. Continue the existing implementation; preserve its read-only viewer while removing its fallback data. Panel integration and browser acceptance belong to part 2.

1. Remove decision types, endpoints, query options and tests, and decision fixture records. Remove the client evidence fixture and Vite evidence shim. Existing browser tests receive findings only through `page.route`.
2. Mirror `backend/app/findings.py` exactly. `listFindings(runId)` resolves the inspection using the real catalog and RunOut, then filters by database run ID. `getFinding(id)` reads the detail endpoint. Both use the authenticated `readApi` transport with typed HTTP errors.
3. Move contain-fit geometry into `src/lib/outline.ts`, exposing `containFit`, `mapPoint` and `polygonPoints`. Move `formatArea` into `findings.ts` and supply the missing-scale label through all three dictionaries.
4. Add a node-side production-source fixture scan. Test exact URLs, Authorization, literal fixture responses, HTTP 500, run associations, area strings and fixed outline coordinates. Replace obsolete fallback assertions with empty/error assertions; retain existing read-only panel tests.
5. Run `pnpm lint`, `pnpm typecheck`, `pnpm test`, `pnpm build`, then inspect the built client for fixture records. Do not start servers, capture screenshots or run Playwright; Kerege handles browser execution.

## Contract details

DEF1 has no run-findings route: list is `/api/inspections/{id}/findings`. `EvidenceOut` contains `frame_id`, `image_url`, `outline`, `confidence`; it does not contain width/height. Do not invent response fields. The existing renderer obtains dimensions from manifest camera metadata or the loaded image. Confidence stays in the API type and is never rendered.

Set `retryOnMount: false` on findings queries so the existing loading/ready handoff cannot restart a failed request. A single panel instance and further viewer integration remain part 2.

## Licences

Checked installed package metadata: React, Three.js, TanStack Query, Vitest, Testing Library, Vite, ESLint and Prettier are MIT; Playwright and TypeScript are Apache-2.0. All permit commercial use. No dependencies or models added.

## Verification

- `cd web && pnpm lint && pnpm typecheck` passed.
- `cd web && pnpm test && pnpm build` passed: 43 test files, 818 tests, including source fixture scan, i18n key parity, mocked API failures and both error/empty loading-to-ready regressions.
- Inspected built JavaScript for the fixture run ID, comment, scale source, confidence sentinel, fixed area string and fixture import path: none are present. Ordinary translated class labels remain available.
- Kept existing authentication error messages compatible while adding typed HTTP status/path fields. Reuse successful detail responses for 30 seconds across the existing viewer handoff; failed requests have `retryOnMount: false`.
- Vite retains its existing large-chunk advisory. No server, screenshot capture or Playwright run was started.

## Remaining: part 2

Complete viewer/panel integration and browser acceptance against the demo seed. Existing browser tests now source findings via `page.route` and wait for `window.__qarau.pointsLoaded`; moving the compiled-preview build to one shared setup remains part of the part-2 e2e work. Kerege runs the browser tests and the tester captures reference screenshots.

## Compiled-preview readiness repair

Kerege's four compiled-preview cases timed out waiting for `window.__qarau.pointsLoaded`: the viewer published that object only inside `import.meta.env.DEV`, which Vite removes from production builds. Publish the readiness flag after successful engine initialization in every build and clear it on unmount. Keep engine access, benchmark controls, dense debug helpers and the FPS timer dev-only. The existing compiled-preview wait, assertions and timeouts remain unchanged; dense e2e calls only gain TypeScript non-null assertions for helpers that are intentionally absent in production.

Add a regression that disables DEV, holds geometry loading unresolved, checks readiness is absent, resolves the real viewer bundle, checks the exact `{ pointsLoaded: true }` object with no debug methods, then unmounts and checks cleanup. No dependency or design changes. Browser execution remains with Kerege; no local server or screenshot capture.

Repair verification: `pnpm lint`, `pnpm typecheck`, `pnpm test` (43 files, 819 tests) and `pnpm build` all passed. Inspected the compiled JavaScript: the unconditional readiness assignment is present; dev-only `densePointScreen` and finding fixture records are absent. `git diff --check` passed. Kerege must rerun Playwright; no local servers were started.
