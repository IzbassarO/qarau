# Inspection list adapter alongside the grouped catalog

Date: 2026-10-26. Status: accepted for UI-inspections part 1.

B1's GET /api/inspections returns an array of InspectionOut: id, site_id, mode,
status and created_at. Object names come from GET /api/sites. The list must not
substitute /api/runs when inspections are empty. Findings counts remain null until
the API supplies findings_count. A supplied run_id is retained; its absence is valid.

An uploaded B1 inspection can still say created. Resolve that case through the S1
detail endpoint and the existing run adapter, which uses an explicitly identified or
sole run, never UUID ordering. An unresolved or unfamiliar state is an API error,
not a fabricated uploaded/done status in the new five-state list.

The existing grouped catalog remains available through listInspections and the boolean
useInspections(enabled) overload. The new useInspections() call returns flat summaries
under the cache key ['inspections', 'list']; existing prefix invalidation still works.
Its stricter InspectionSummary type is separate from the old SiteInspectionSummary
used by Site and InspectionView. The existing page behavior is unchanged in part 1.

Rows use v8 ListRow, StatusText and secondary buttons. Workflow states stay neutral:
signal colours are reserved for findings and coverage. Rows receive whole-summary
callbacks; route/action wiring is deferred to part 2.

S1's exact step retry route is POST
/api/inspections/{inspection_id}/runs/{run_id}/steps/{step}/rerun. Re-queueing the
remaining workflow uses POST /api/inspections/{inspection_id}/runs with optional
run_id (no force by default). Part 2 must resolve the detail/failed step before
choosing a step retry; part 1 performs no writes.
