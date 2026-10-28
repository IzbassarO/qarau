# UI-report plan

## Scope and sequence

Part 1 only: authenticated report route, session-aware typed API adapter, API summary,
PDF language choice, generating/ready/failed views, three locales and Vitest coverage.
Part 2 follows tester acceptance: actual iframe, download/hash UI, inspection/viewer links,
phone refinement and Playwright fixture download. No report editing, XLSX or email.

## Contracts checked before implementation

- Read `CLAUDE.md`, `docs/design/DESIGN.md`, `report-light.png`, and backend
  `reports.py`, `inspections.py`, `findings.py`, `decisions.py`, `crud.py`, `runs.py`, `seed.py`.
- RPT1 generates synchronously: 200 is the PDF body; `X-Report-SHA256` is its hash.
  There is no generating response in the current route: pending requests are generating.
  Also recognize HTTP 202 as generating without treating its body as a PDF.
- Failures use FastAPI's `detail` (string, or validation-detail array for 422).
  Preserve API messages; use localized fallback copy only when there is no message.
- Load `/api/inspections/{id}`, `/api/sites`, `/api/inspections/{id}/findings`, and
  `/api/findings/{id}/decisions`. Findings already carry effective class, severity and
  status: never replay decisions in the client. Show the returned decision history and names.
- The API currently exposes site name, not a separate asset record. Show that object name.
  Scale source is per finding. No coverage JSON endpoint exists; show “coverage not computed”.
  Do not derive coverage, areas, risk, or PDF content in the browser.
- Seeded inspections have database-generated integer IDs, not slugs. The requested
  `/inspections/notre-dame/report` is a compatibility alias for seed run `notre-dame-p3e`;
  resolve it through inspection/run JSON associations, never assume an inspection ID.
  Numeric inspection URLs remain canonical. Unknown IDs retain the API failure text.

## Implementation

1. Extend the existing API transport with raw authenticated response access and error-text
   reading while preserving existing consumers' status-based errors.
2. Add `report.ts`: typed JSON summary fetcher, decision history, PDF Blob/hash state union.
3. Use the v8 full-width report scaffold, top language segment and centered `qv-doc`,
   including its white document appearance in every theme. Reuse existing components and
   tokens only. Render a ready placeholder for part 1; do not add the iframe/download yet.
4. Register the route behind `RequireAuth`, within `AppShell`, without the inspection
   selection/error views masking report failures. Translate all new copy into ru/kk/en.
5. Add literal API fixtures and tests for summary, absent coverage, state responses,
   hash, exact failure text, alias resolution, unknown IDs and language refetches.

## Verification and licences

Existing installed packages checked via package metadata: React/React DOM 19.3.0,
react-router 8.4.0, TanStack Query 5.104.2, Vite 8.3.4, Vitest 5.0.4 and
Testing Library React 16.3.3 are MIT and permit commercial use. No new dependencies or models.
Use existing OFL self-hosted fonts. No tools, plugins or instructions installed.

Run `pnpm lint`, `pnpm typecheck`, `pnpm test`, `pnpm build` from `web/`, including
translation-key and token checks. Screenshots belong to the tester; do not take them.
Do not run Playwright locally. No dev/preview server is needed or left running.

## Part 1 result

Implemented the authenticated route within the full-width AppShell, API summary and
decision history, PDF state adapter with Blob/hash, independent PDF language choice,
and ru/kk/en copy. The named demo URL resolves via API run associations; unknown IDs
show the API message. The document uses the existing v8 report components and light
document tokens even when the surrounding app is dark.

Verified: `pnpm lint`, `pnpm typecheck`, `pnpm test` (45 files, 835 tests passed),
`pnpm build`, and `git diff --check`. Tests include literal summary fields, null
coverage, three states, exact hash/error words, authentication, the named demo URL,
unknown IDs, language switching and stale response isolation. Translation-key and
noLiterals checks passed with the full suite. No servers were started. Screenshots
remain with the tester. Part 2 is intentionally pending acceptance of this part.

## Gate correction

Removed the redundant query-retry setting from the ReportPage test client, which the
scope gate rejected as a test-retry override. All assertions and fixture expectations
are unchanged; the page's own API-query behavior remains exercised by the tests.
Repeated lint, typecheck, the full suite (45 files, 835 passing tests), build and
diff whitespace validation successfully. The named report-route test still verifies
the summary and language choice without an error banner. No server was started.

The returned review also reports missing tester screenshots for
`/inspections/notre-dame/report`. Visual acceptance remains pending tester capture
in light/dark and ru/kk/en; the task explicitly reserves screenshots for the tester.

## Localization and visible state correction

The tester found English placeholders in the shared evidence labels used by this
page and an ICU fallback month code in the Kazakh date. Translate the report-used
evidence labels in ru/kk, and format Kazakh dates with explicit translated month
names and local date/time fields. Keep English and Russian document dates localized
through their supported regional locales. No new dependency is needed.

Move the existing language segment and PDF state above the summary, inside the
document, so both are available before lengthy findings. Keep v8 components and
tokens. Preserve every existing test assertion, and add literal regression cases
for translated labels, date text, and controls/state preceding the summary.

Implemented and verified this correction: lint, typecheck, all 46 Vitest files
(857 passing tests), build and diff whitespace validation passed. Existing test
assertions and fixture expectations were preserved; 22 regression cases were added.
The Kazakh formatter is also tested with Intl locale data unavailable. No server
was started and no screenshots were taken; visual confirmation remains with the tester.
