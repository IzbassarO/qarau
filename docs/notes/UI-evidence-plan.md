# UI-evidence — part 1 of 2: trim branch and data layer

This change contains only the data layer. The tester accepted the part-1 criteria but rejected the inherited panel, viewer/engine rewrite and browser work as out of scope. Restore those files to the merged `origin/dev` baseline, including their existing tests. Remove part-2-only components, adapters and their paired tests with the deferred feature. Git history retains that implementation for part 2. Do not alter any findings, outline or fixture-scan assertions.

## Scope

1. Remove the decision APIs and fabricated finding from the branch. The evidence fixture and Vite fixture shim remain absent. Keep the literal finding only in `src/test/fixtures/finding.json`, imported by the API unit test.
2. Keep the typed DEF1 response, authenticated `listFindings(runId)` and `getFinding(id)`, typed HTTP errors and query options in `src/lib/findings.ts` and the existing API transport. Resolve viewer run names through the inspection/run catalog because DEF1 lists findings by inspection.
3. Keep `containFit`, `mapPoint` and `polygonPoints` in `src/lib/outline.ts`; retain fixed expected coordinates for a 1920×1080 frame in a 400×400 box.
4. Keep `formatArea` and only its two translation keys in all three dictionaries: square metres and units with an explicit missing-scale label. Keep the literal expected strings in the unit tests.
5. Keep the node-side scan of non-test TypeScript client sources, including its ban on fixture imports. JSON class translations are ordinary UI copy, not bundled finding records.
6. Run lint, typecheck, the complete Vitest suite and build, inspect the resulting client for fixture records, and verify the aggregate branch diff has no viewer, engine, auth image, bundle, Vite middleware or e2e changes. Do not start a server or run Playwright in this sandbox.

## API contract

`backend/app/findings.py` declares `/api/inspections/{id}/findings` and `/api/findings/{id}`. `EvidenceOut` has `frame_id`, `image_url`, `outline` and `confidence`; it has no width/height fields. Mirror the actual API without inventing fields. Part 2 must obtain image dimensions from the run manifest or the loaded image and must never display confidence.

## Licences

Previously checked installed package metadata: React, Three.js, TanStack Query, Vitest, Testing Library, Vite, ESLint and Prettier are MIT; Playwright and TypeScript are Apache-2.0. All permit commercial use. No dependencies or models added.

## Remaining: part 2

The read-only panel, finding selection/deep links, viewer integration and evidence browser tests are deferred together. This part does not add a finding panel or change the current viewer behavior.

## Verification after scope correction

- `pnpm lint`, `pnpm typecheck`, `pnpm test` (41 files, 791 tests) and `pnpm build` passed. The existing Vite chunk-size advisory remains.
- Findings, outline, fixture-scan tests and their JSON fixture are byte-for-byte unchanged from the accepted previous round.
- Viewer, engine, existing browser tests, SessionImage, bundle types and Vite middleware are byte-for-byte equal to `origin/dev`. The aggregate change against that merged baseline contains only the 11 part-1 files described above.
- Production JavaScript contains none of the literal finding fixture identifiers, comments, scale source, confidence sentinel, fixed area string or evidence-fixture import path. `src/fixtures/evidence.ts` remains absent.
- `git diff --check` passed. No servers, screenshots or Playwright runs were started.
