# UI-inspections, part 2

Build on the accepted flat summaries and v8 rows. Mount a start-page component in
AppShell at `/`, retaining the grouped Sidebar and existing inspection progress
routes. Leave the viewer route and viewer source untouched.

Use S1 detail metadata to resolve a missing map run ID and the failed step for
Retry. Send the step rerun through `lib/api.ts`, invalidate inspection queries,
and display failures without synthesizing statuses or counts.

Add a literal five-status fixture at `/?demo=inspections`, with inert row actions,
and document that choice in an ADR. Fit the existing v8 rows to the phone layout
using spacing, sizing and stacking only.

Verify start-page and action integration with Vitest, and add mocked API
Playwright coverage for both configured viewports and en/ru/kk. Update existing
start-page assertions to the new table while preserving sidebar compatibility
checks. Run lint, typecheck, Vitest and build. Kerege runs Playwright and captures
screenshots; no server or screenshots are started here.

No dependencies added. Existing package licence files confirm React, React Router,
TanStack Query, Vite, Vitest and Testing Library use MIT; Playwright uses
Apache-2.0. Both permit commercial use. Existing self-hosted fonts remain under OFL.

## Verification

Implemented the start page, actions, fixed demo, phone stacking and translations.
AppShell integration tests cover the top button, map run resolution, progress,
successful and rejected retries, API recovery and inert demos in all locales.

Final checks: `pnpm lint`, `pnpm typecheck`, `pnpm test` (38 files, 766 tests)
and `pnpm build` pass. `git diff --check` passes. Viewer sources, ViewerPage and
the viewer router configuration have no changes. Playwright discovers 30 new
list tests across desktop and phone; Kerege must run them and the updated existing
specs. Screenshots remain with the tester. No servers were started.
