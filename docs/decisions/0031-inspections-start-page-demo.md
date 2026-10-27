# Inspections start page and repeatable demo

Date: 2026-10-26. Status: accepted for UI-inspections part 2.

`/` presents the accepted v8 InspectionsList. `/?demo=inspections` selects a fixed
five-row fixture in `web/src/fixtures/inspections.ts`, one inspection per workflow
status, in newest-first order. Running and done have literal findings counts; the
other counts are null. This query does not affect `/inspections/:id`, the existing
progress demo, authentication or `/viewer/:runId`.

The fixture disables both inspection queries and all row actions. It is available
in production builds for repeatable tester screenshots in every locale and theme,
without loading real inspection data. The New inspection dialog remains available
and uses the existing upload flow when explicitly opened.

Open map uses the API run ID, resolving it from S1 detail if absent in the list.
Show progress navigates to the existing inspection detail view. Retry reads the
detail's failed step and current run and posts to S1's step rerun route through
`lib/api.ts`. Missing identifiers or steps are shown as an action error; the client
does not choose a processing step or invent a new status. Successful retries
invalidate the inspection list, sidebar and detail queries.

The grouped sidebar retains its existing legacy-export compatibility. The main
list uses inspection CRUD exclusively, including an authoritative empty array.
When CRUD is unavailable on a read-only catalog, the main area shows an unavailable
notice while the sidebar's exported runs remain navigable.

On phones the same ListRows stack into the v8 phone cards, with dates, status,
counts and a minimum 44 px action target. Column headers remain accessible to
assistive technology. No filters, search, pagination or evidence UI is added.
