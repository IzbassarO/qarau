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

## E2e return: duplicate language request

Reproduce the extra Kazakh PDF fetch in Vitest with the application's StrictMode
wrapper and a pending PDF response. Keep the existing e2e request-array assertion.
Move the PDF query observer outside the language-keyed preview lifecycle so StrictMode
replaying a new preview's effects cannot cancel and restart its network request.
Retain request cancellation, generation polling, retries and the 28-second deadline.
Run the normal lint/typecheck/Vitest/build checks with the now-installed pdfjs-dist;
Kerege runs browser e2e. No servers or screenshots locally.

The new regression failed before the fix with en/kk/kk and passes after it with
exactly en/kk, both while loading and after painting. Normal checks now pass with
the real installed dependency: lint, typecheck, all 970 Vitest tests and build.
Playwright discovers all 32 report scenarios; its assertions are unchanged.
Browser execution remains Kerege's check. No servers were started.

## Readiness return: pending requests at the deadline

The screenshot gate waits for both aria-busy and network idle. The current timeout
only disables the PDF query; disabling a TanStack query does not cancel a fetch already
in progress. Reproduce that with an abort-aware mock before changing the deadline.
Cancel this report's summary/PDF queries at the deadline, prevent focus/reconnect from
starting background report requests, and retain one absolute deadline per attempt.
Add StrictMode tests through the real devReport middleware and a default-API browser
matrix covering all three locales, both themes and the existing two viewports. Keep
all existing assertions. Browser execution/screenshots remain Kerege's responsibility.

Confirmed regression: both hung summary and hung PDF tests failed before the fix
because the active fetch signal was not aborted at the deadline. They now pass,
including a zero pending-request/query count. A fake-clock test keeps the original
28-second deadline through five summary updates while rendering is stalled. The
worker/document is destroyed exactly once after page one finishes and before ready
is announced; Blob download and hash remain available.

Verified: pnpm lint, pnpm typecheck, all 977 Vitest tests and pnpm build pass.
Playwright discovers 44 report tests, including the 12 new default-dev readiness
cases after sign-in (ru/kk/en x light/dark x desktop/phone). Existing e2e assertions
are unchanged. Real browser execution and all screenshots still require Kerege;
no server or screenshot was started here.

## Font readiness return

The remaining in-flight resource is IBM Plex Sans 600, not an API request. Preserve
all existing e2e assertions. Request the report's v8 font families/weights explicitly
using the design tokens and the rendered text (Latin/Cyrillic/Kazakh), then await
FontFaceSet.ready before announcing the painted preview as ready. Keep the existing
absolute deadline and Blob download behavior. Add a delayed-font/load/layout regression
and strengthen the 12 default-dev cases with font readiness assertions. Run the normal
local checks; Kerege owns first-attempt browser e2e and screenshots. No servers locally.
