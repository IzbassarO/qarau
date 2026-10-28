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

## Part 2 plan

Build on the accepted summary, language selector and API states without replacing them.
Use the returned PDF Blob for one object URL shared by the iframe and download; revoke
it when the PDF changes, the language changes or the page unmounts. Show a shortened
header hash beside the download with its complete value in a title. Keep the summary
above the preview and the download in the sticky top bar, including at 390 × 844.

Add localized Report links for done inspections and for viewers with a known inspection
association. Preserve API-backed numeric inspection IDs and the existing demo report alias.
Add `/inspections` as a list route. Extend the current Vitest coverage for Blob URL
lifecycle, download filename/hash and both entry links. Add a hand-authored PDF under
2 KB and a Playwright spec mocking authentication, JSON and PDF routes; exercise download
filename and literal fixture byte size at both configured viewports in all three languages
and light/dark. Kerege runs the e2e suite; no local server or screenshots will be started.

Licence check (installed package metadata): React 19.3.0, react-router 8.4.0 and
TanStack Query 5.104.2 are MIT; Playwright 1.64.0 is Apache-2.0. All permit commercial
use. No dependencies, models, plugins or downloaded tools are added. The fixture PDF
contains only original test text.

Run lint (ESLint + Prettier), typecheck, full Vitest and build. Validate the Playwright
spec through TypeScript and test discovery without starting its server. Screenshot and
e2e execution results remain with Kerege/tester, as required by this task.

## Part 2 result

Added the inline browser PDF iframe and download from the same Blob URL, with the
route/language filename, shortened SHA-256 beside the button, and full header value
in its title. URL cleanup runs on PDF, route or language change and on unmount; a
pending or failed replacement cannot retain the previous preview/download. The
sticky v8 top bar keeps the download reachable on phones, with touch-size controls
and the summary stacked above the preview. Replaced the ready placeholder sentence
with localized ready copy now that the preview exists.

Added done-only list Report links, `/inspections` as a list route, and a viewer link
using API inspection/run associations (including the named demo and inspections
without findings). Expanded the existing Vitest tests for links, states, the hash,
filename, shared Blob and URL cleanup. Added `web/e2e/report.spec.ts` and an original
606-byte one-page PDF. Its mocked-API scenarios cover sign-in, summary, hash, language,
actual fixture download and literal byte size, phone control reachability, the demo
alias and unknown-id API errors. The existing Playwright config discovers 14 cases
across its desktop/phone projects; its fresh-server configuration is unchanged.

Verified: `pnpm lint` (ESLint and Prettier), `pnpm typecheck`, `pnpm test` (46 files,
867 tests passed), `pnpm build`, Playwright discovery (`--list`) and `git diff --check`.
No new dependencies. No server was started and no screenshots were taken. The full
Playwright run and light/dark ru/kk/en screenshots remain with Kerege and the tester.

## Continuation after round 4

Read the four task commits and the latest packet at `.state/packets/UI-report.md`.
The last gate stopped on the tester's oversized-image API request, not a reported
application regression. Preserve the existing page, summary, states and v8 styles.

Close the remaining current acceptance gaps: resolve `/inspections/demo/report`
through the seeded `notre-dame-p3e` run association, expose that route from
`/viewer/demo`, and add temporary `?theme=light|dark&lang=ru|kk|en` presentation
overrides without changing saved preferences. Show the complete header hash beside
download, wrapping it within the existing toolbar. Move the original valid PDF
fixture into `src/test/fixtures` and strengthen e2e to compare downloaded bytes,
keeping its literal size check. Exercise the actual demo route and URL overrides.

Installed package metadata rechecked: React, react-router, TanStack Query, Vitest
and Testing Library use MIT; Playwright uses Apache-2.0. All permit commercial use.
No dependencies or models added. Run lint, typecheck, full Vitest, build and e2e
discovery. Kerege runs browser e2e and screenshots; do not start a server here.

### Continuation result

Implemented the demo alias and viewer link, the full visible hash with wrapping,
and read-only URL presentation overrides shared by all routes. The demo viewer's
Report link has a 44 px phone touch target. Existing numeric and notre-dame routes
and the summary/state behavior are preserved. Example tester URL:
`/inspections/demo/report?theme=dark&lang=kk` (using the normal authenticated session).

The valid original 606-byte PDF now lives at `web/src/test/fixtures/report.pdf`.
E2e retains its literal size/hash checks and additionally compares every downloaded
byte with that fixture. It covers numeric report routes across all six theme/language
variants, URL overrides against different stored/system preferences, and navigation
from the demo viewer to the seeded report. Vitest adds demo alias regressions and
temporary override, navigation, fallback and invalid-value cases.

Verified after the final code change: `pnpm lint && pnpm typecheck`,
`pnpm test && pnpm build` (47 files, 878 tests passed, including keys/noLiterals),
Playwright report-spec discovery (14 desktop/phone cases), `git diff --check` and
the fixture's SHA-256 against the fixed e2e header literal. No servers started,
no screenshots taken, no dependencies added. Browser e2e execution and screenshots
remain with Kerege/tester as instructed.
