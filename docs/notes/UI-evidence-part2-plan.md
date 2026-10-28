# UI-evidence — part 2 of 2

Build on the accepted API and outline helpers. Render a single read-only evidence panel in ViewerPage outside the cloud-loading/ready branches; query findings once per run with mount refetch disabled. Resolve URL selection from the real run findings and preserve other search parameters when selecting or closing.

Use the existing v8 detail, facts and thumbnail components from finding-light.png. Show only API class, severity, effective status, area and source frames with SVG polygons. Use manifest dimensions, falling back to the authenticated image's natural dimensions. No confidence, decisions, actions, assistant or fabricated finding data.

Pass API point indices and severity into the existing engine without rebuilding the cloud. Route existing point picking and finding markers to the same URL selection. Keep ordinary point evidence working.

Add panel state and regression tests with the existing test fixture. Add an evidence-preview browser spec with page.route API mocks, a single production build shared by its projects, and a pointsLoaded wait before panel assertions. Reuse the existing Playwright server; do not edit gate configuration or start a server locally.

Verify lint (including Prettier), typecheck, all unit tests, build, client fixture exclusion and diff scope. Browser execution and screenshots belong to Kerege.

Installed package licences checked in package.json: React, Three.js, TanStack Query, Vitest and Testing Library are MIT; Playwright is Apache-2.0. All allow commercial use. No dependencies or models added.

## Implementation and verification

- Added the v8 read-only evidence card and authenticated image load dimensions. The three frames contain SVG polygons, with a large first frame and two smaller frames. No decision actions, log, assistant or confidence display was added.
- ViewerPage owns one panel across cloud loading and ready states. Finding URL changes preserve the viewer instance; selecting a point or marker updates the URL and the engine selection. The findings query disables retries on mount and mount refetch.
- Real API point indices update severity colours without rebuilding cloud positions. Existing ordinary point, navigation and optional-layer warning tests remain intact; their render helper now supplies QueryClientProvider.
- The browser spec builds once during Playwright collection (workers reuse the build), serves production shell/assets through the existing fresh server, mocks the API through page.route and waits for pointsLoaded before all panel assertions. Gate configuration and reuseExistingServer remain unchanged.
- ESLint, Prettier, typecheck, all 804 Vitest tests and production build pass. The build retains the existing chunk-size advisory. Findings/outline literal tests and fixture-exclusion checks pass; production JavaScript contains none of the finding fixture record identifiers, comment, scale source, area literal or confidence sentinel.
- English evidence copy is wired through the existing dictionaries; full localization and the dedicated phone layout remain UI-evidence-c. Browser execution and real-demo-seed screenshots remain with Kerege, as requested. No servers or screenshots were started here. No dependencies or models were added.
