# UI-report-s1

Keep the existing report page, routes, summary adapter, translations and tests.
Do not change e2e, screenshots, backend or design styles.

1. Extract the existing pdf.js worker/loading/rendering lifecycle into
   `web/src/lib/pdfPreview.ts`, retaining cancellation, cleanup and readiness.
2. Add a focused acceptance test for summary, loading, ready, API failure and
   preview failure using mocked API responses and pdf.js. Pin the download hash
   to a literal fixture value and verify the canvas/page count and absent frames.
3. Verify report translations and run lint, typecheck, Vitest and build.

Contract checked in `backend/app/reports.py`: GET
`/api/inspections/{inspection_id}/report.pdf?lang=ru|kk|en` returns PDF bytes,
`X-Report-SHA256`, `X-Report-Generated-At` and `Content-Disposition`. There is no
report JSON endpoint, status field or page count. Keep the existing authenticated
binary transport in `lib/api.ts` and summary fetched through inspection, site,
finding, decision and coverage APIs in `lib/report.ts`; do not invent a JSON API
or fetch a second generated PDF. `report.json` records the HTTP response envelope
for the binary endpoint; existing summary fixtures retain their API shapes.

Licences checked locally in installed package metadata and pdfjs-dist/LICENSE:
pdfjs-dist 5.4.624 is Apache-2.0, permitting commercial use. React, React Router,
TanStack Query, Vite, Vitest and Testing Library are MIT. No dependencies added.

Completed: extracted the preview lifecycle without changing the page layout or
binary API transport. Added abort regressions for document/page loading, a pending
summary API state test, literal hash assertions and report translation/placeholder
checks. Existing mocked pdf.js load/render failure, three-page count, summary,
download, retry and no-frame assertions remain intact and pass.

Verified: `pnpm lint`, `pnpm typecheck`, `pnpm test` (56 files, 983 tests) and
`pnpm build` pass. The build bundles the pdf.js worker; Vite retains its large-chunk
warning. No servers started. E2e and screenshots were not changed or run; those
remain with UI-report-s2/s3 and Kerege.

## E2e return: requests from the document before report navigation

Kerege reports a painted, network-idle report with three requests still tracked:
IBM Plex Sans 600 Cyrillic/Latin and `/api/runs`. The report does not request the run
catalog. Signing in at `/` mounts the inspection shell immediately before full
document navigation. Its catalog requests are not explicitly aborted on pagehide,
and its semibold fonts first start loading after the login form disappears.
Also, login publishes the session after fetch headers without consuming the body.

Add regressions for departure with a pending catalog, delayed login-response bytes
and delayed interface fonts. Consume authentication response bytes before success,
finish interface font loading before handing off from login, and cancel inspection
queries on pagehide (refetch active queries on back/forward-cache restoration).
Reuse the report's existing token-based font preparation. Keep every existing
e2e/Vitest assertion unchanged and run the normal checks without a server.

The new regressions failed before the fixes and pass afterwards: authentication
waits for response bytes, interrupted bodies do not publish a session, font loading
and layout finish before the login handoff, and pagehide aborts the pending catalog.
Restoring the document from bfcache refetches the active inspection queries. Font
failure allows fallback fonts without blocking valid authentication.

Two existing unit-test fetch mocks now return a fresh Response per login instead
of reusing one consumed stream (auth identity and viewer sensitivity tests). Their
assertions and fixed expected values are unchanged. No e2e files changed.

Verified after the fix: `pnpm lint`, `pnpm typecheck`, `pnpm test` (56 files,
988 tests) and `pnpm build` pass. No servers or browser screenshots started.
Playwright's cross-document network-event behavior still requires Kerege's rerun;
the jsdom regressions verify the application lifecycle, not Chromium networking.
