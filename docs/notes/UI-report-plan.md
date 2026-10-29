# UI-report continuation, 2026-10-29

Build on the existing report page, summary, routing, locales and link tests.
Baseline lint/typecheck and all 956 Vitest tests pass. Remaining acceptance gaps:

- Replace the iframe with pdf.js page-one canvas rendering and a page count.
- Add pdfjs-dist and its Vite-bundled worker; keep Node canvas out of the browser.
- Move authenticated binary PDF fetching into lib/api.ts, preserving API error text
  and X-Report-SHA256 (confirmed against backend/app/reports.py, read-only).
- Keep busy until painting completes; clear it on load/render failure or before 30 s.
  Preserve Blob download/hash even when preview rendering fails.
- Update existing Vitest/e2e assertions for canvas pixels, page count and failures.
- Verify empty coverage and the default dev-server demo path without shipping findings.

Keep v8 components/tokens and existing responsive layout; screenshots and browser e2e
belong to Kerege. Do not start servers. Run lint, typecheck, Vitest, build and e2e discovery.

Licences: pdfjs-dist Apache-2.0 (mozilla/pdf.js v5.4.624 LICENSE, commercial use
permitted); optional @napi-rs/canvas MIT, Node-only. Existing React/router/query/Vite/
Vitest/Node MIT, Playwright Apache-2.0, fonts OFL. Fixture PDF is original project content.

Dependency installation initially failed with npm registry ENOTFOUND; continue with the
implementation and report any verification limitation accurately.

Implemented: canvas page-one rendering, bundled-worker import, page count, rendering
failure/download separation, retry/cancellation and 28-second deadline; binary transport
in api.ts; getInspection-backed summary and empty coverage handling; all three locales.
Existing list/viewer report-link tests were already present and are retained. Dev-only
API now serves the same synthetic demo site/date with no findings and uncomputed coverage.
The original fixture has exact xref/stream lengths and a vector rule; 1 page, 619 bytes,
SHA-256 a278f21895176e40699b4de5dda3b7aa385e82e13995d4bab0beff341aa5d558.
E2e asserts pixels/count/download in both viewports and all locale/theme combinations.

Verification limitation: npm registry DNS remains unavailable; pdfjs-dist is declared
and pinned in package/lock (integrity from cached npm metadata), but the package archive
is absent. The lock entry contains no optional Node adapters. Standard typecheck/build
and the ReportPage suite cannot resolve pdfjs-dist until it is installed. A separate
scratch-only Vitest config resolves just the already-mocked pdf.js imports to diagnostic
placeholders, allowing state/summary tests to execute; this does NOT verify the real
library, worker or production bundle and is not shipped or used by CI. Kerege must run
real browser e2e and screenshots; no server or screenshot was started here.
