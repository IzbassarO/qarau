# UI-report plan

## Scope and implementation

Build on the existing report work. Changes stay in `web/` and this note; the
backend is read-only. No report editing, XLSX, email, new dependencies or models.

1. Use RPT1's authenticated `GET /api/inspections/{id}/report.pdf?lang=ru|kk|en`.
   Read the exact `X-Report-SHA256` header, return its `sha256` and the PDF Blob,
   and throw HTTP errors carrying the API detail/status. Pending requests and
   HTTP 202 show generating; a failed response shows the API message and retry.
2. Load the summary from inspection, sites, findings and append-only decisions
   endpoints. Display object/site name, date, severity groups and counts, returned
   statuses/deciders and scale sources. Never replay decisions or compute coverage.
   There is no coverage JSON endpoint yet, so show localized coverage-not-computed;
   render a supplied coverage line verbatim when available.
3. Register `/inspections/:id/report` behind `RequireAuth`. Keep language choice
   independent of UI language. Share one Blob URL between iframe and native download
   anchor; revoke it on replacement/unmount. Generating sets `aria-busy=true`; ready
   and failed clear it. A replacement cannot retain a stale preview/download.
4. Link done inspections and viewers with known inspection/run associations to
   their reports. Resolve named `demo`/`notre-dame` report aliases through the API's
   seeded `notre-dame-p3e` run association, never a hard-coded inspection ID. Preserve
   unknown-ID and authorization errors.
5. Follow DESIGN.md v8, `report-light.png` and existing tokens/components. Preserve
   the tester-requested toolbar language controls, shortened hash beside download
   (complete header value in its title), disabled download while unavailable,
   desktop summary/preview columns and phone preview before the full summary.
   Keep phone controls at least 44 px and prevent horizontal overflow. The document
   stays white across app themes. All UI strings use matching ru/kk/en keys; explicit
   Kazakh month names avoid browser ICU fallback codes. Read-only `theme`/`lang` URL
   overrides do not change saved preferences.

## Tests and returned corrections

Keep all existing test assertions and fixture expectations. Vitest covers summary
literals, severity counts/status/decider, date/scale/coverage, three PDF states,
authentication, retry, language query parameters, Blob lifecycle and entry links.

Playwright uses mocked API routes and the original 606-byte PDF under
`web/src/test/fixtures/`. Check the literal header hash, download filename/size and
fixture bytes at desktop and phone sizes in ru/kk/en and light/dark. After switching
the named report to Kazakh, wait for requests `['en', 'en', 'kk']` and the anchor's
literal `download="report-notre-dame-kk.pdf"` plus Blob href before clicking; an
existing English Blob iframe alone does not establish readiness.

Six additional theme/language scenarios start at `/inspections`, click the localized
Report link and assert the ready report summary, language choice, iframe, named
anchor and hash without an error banner or raw translation keys. The named viewer
link also reaches the same API inspection report.

## Verification, licences and tester handoff

Required local checks: `pnpm lint && pnpm typecheck`, `pnpm test && pnpm build`,
Playwright discovery and `git diff --check`. The last code round passed all checks:
47 Vitest files, 885 tests and 28 discovered desktop/phone report e2e cases. Kerege
runs browser e2e with its own fresh server (`reuseExistingServer: false`). Never
start a local server or take developer screenshots for this task.

Tester screenshot flow: authenticate against the seeded API, set the desired saved
UI language and light/dark system theme, open `/inspections`, click a done
inspection's Report action, then wait for the PDF section to clear `aria-busy` and
show the preview/download. Capture the report route at 1440x900 and 390x844 for all
six theme/language combinations. The mocked equivalent is `report reached from
inspections` in `web/e2e/report.spec.ts`. Browser execution and screenshots remain
with Kerege/tester; protected pipeline configuration is outside this task.

Installed package metadata: React/React DOM, react-router, TanStack Query, Vite,
Vitest and Testing Library use MIT; Playwright uses Apache-2.0. Existing self-hosted
fonts use OFL. All permit commercial use; no dependencies or tools were added.

The size-gate correction consolidates repeated historical plans/results into this
current note. Product code, tests, assertions and fixtures are unchanged.
