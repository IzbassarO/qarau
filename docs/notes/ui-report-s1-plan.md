# UI-report-s1

Scope: retain the existing report page/API adapter, extract the pdf.js preview,
verify the summary, loading/ready/failed states, page count, download hash and
ru/kk/en translations. Preserve every existing test/assertion. No e2e or screenshot
changes, backend edits, servers, commits or new dependencies.

API checked read-only in `backend/app/reports.py`: GET
`/api/inspections/{inspection_id}/report.pdf?lang=ru|kk|en` returns PDF bytes and
`X-Report-SHA256`, not report JSON. Retain the existing authenticated binary
transport and inspection/site/finding/decision/coverage summary reads. The
`report.json` fixture records the HTTP response envelope, not an invented endpoint.

Completed: preview wrapper with worker cleanup/cancellation, literal hash and
fixture-backed state assertions, translation/placeholder checks. E2e returns
required font readiness before login handoff, completed authentication response
bodies, outgoing query cancellation and one initial catalog reuse. The catalog is
session/user scoped, memory only, single use and expires after 5 s; refreshes fetch
the server. StrictMode regression: three run-catalog calls before, one after.

Licences checked in installed packages: pdfjs-dist 5.4.624 Apache-2.0; React,
Router, TanStack Query, Vite, Vitest and Testing Library MIT. Commercial use allowed.

Latest verification: lint, typecheck, all 996 Vitest tests and build pass.
Playwright remains Kerege's check. No servers started.

Size-gate return: origin/dev includes inherited UI-report work (196,618 characters
excluding the lockfile); s1 since 76ac332 is 45,325 characters. Existing tests alone
account for 107,440 characters in the cumulative diff. Remove obsolete parent-plan
history and the unused PDF fixture generator; retain the fixed PDF fixture and all
assertions. Resolving the remaining size limit needs the s1 baseline corrected or
explicit permission to move inherited s2/s3 tests; both cannot be decided by
editing protected orchestrator files.
