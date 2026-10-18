# Region of interest — version 1

One optional region of interest (ROI) belongs to a site. It is a world-coordinate,
axis-aligned box, independent of the browser, camera orientation, point ordering,
and whether clipping is enabled. P4c can read this document directly from the run
catalog without opening the viewer.

```json
{
  "version": 1,
  "site_id": "notre-dame",
  "source_run": "notre-dame",
  "frame": "reconstruction",
  "up_axis": "z",
  "min": [-15.25, -22.0, -3.5],
  "max": [17.75, 19.5, 28.5],
  "created_at": "2026-10-17T12:00:00.000Z",
  "created_by": "engineer"
}
```

| Field | Meaning |
| --- | --- |
| `version` | Required integer `1`; readers reject unsupported versions. |
| `site_id` | Required site identifier; the document's id must match the URL/path. |
| `source_run` | Required run or video id on which the box was drawn. |
| `frame` | Required coordinate-frame name. The viewer currently writes `reconstruction`, meaning the exported bundle's world coordinates. |
| `up_axis` | Required `x`, `y`, or `z`; the viewer's exported world is currently Z-up. |
| `min`, `max` | Required XYZ triples of finite JSON numbers, in model units. Every `min[i] <= max[i]`; boundaries are inclusive. |
| `created_at` | Required ISO 8601 timestamp with time and timezone, written when saving. |
| `created_by` | Required user identifier. M1 uses the signed-in UI identity because the shared-token API has no individual account identities yet; this field is not a verified audit identity. Direct development viewer routes without a UI session write `development`; production requires the signed-in identity. |

No scale or geographic coordinates are implied. A pipeline consumer must use the
named frame and explicit bundle calibration before reporting metres or areas.
The validator rejects malformed JSON, missing fields, empty metadata, unsupported
versions, invalid timestamps, non-finite coordinates, and reversed box bounds.
Additional JSON fields are ignored and omitted when rewriting version 1.

## Drawing and vertical extent

Drawing first switches the existing view gizmo to top view. The rectangle's four
screen corners are unprojected onto a horizontal plane at the estimated terrain's
Z intercept (the pure helper accepts an explicit plane coordinate, defaulting to zero);
reversed drags produce the same normalized ground rectangle. Zero-width and
zero-height drags are rejected. The other two axes retain the drawn bounds.

Among finite object-cloud points inside that inclusive ground rectangle, let the
smallest and largest up coordinates be `low` and `high`. The saved up-axis bounds
are `low - 0.05 * (high - low)` and `high + 0.05 * (high - low)`: a 5% margin at
each end. A flat selection has zero up-axis thickness. A rectangle containing no
object points is rejected. The optional far layer does not inflate this extent;
once enabled, clipping applies the same box to both object and far points without
changing either source buffer.

## Site resolution and coordinate-frame caveat

The viewer resolves the current run against the inspection catalog. It uses the
run's `site.id`, then `site_id`, when supplied. If neither association exists, the
run id itself is the site id: `/viewer/notre-dame` saves under site `notre-dame`.
Unassigned runs do not share the inspection-list display group
`qarau:unassigned` as a storage id. Later inspections with the same actual site
association load the same saved document.

**Until P5 (gravity and scale) and M4 (registration), each run has its own
reconstruction frame. A box reused on a different run is therefore only
approximate.** The viewer displays a translated warning when `source_run` differs
from the current run. It does not silently transform, register, or rescale the
box. A pipeline consuming a different run must treat this mismatch explicitly
rather than assuming alignment.

## Catalog storage and API contract

The catalog is file based, as in `backend/app/runs.py`. The ROI path is:

```text
<QARAU_RUNS_DIR>/sites/<site_id>/roi.json
```

The writer validates before creating directories, writes a uniquely named temp
file in the site's directory, then atomically renames it to `roi.json`. Readers
see either complete previous or complete new JSON. Site ids must match
`[A-Za-z0-9][A-Za-z0-9._:-]*` and cannot contain `..`; encoded separators,
traversal, and symlinked site/file paths are rejected. The root catalog directory
is trusted configuration. Saves replace a site's current ROI; this is editable
site configuration, not an append-only finding decision log.

| Request | Declared role | Result |
| --- | --- | --- |
| `GET /api/sites/{site_id}/roi` | `viewer` | `200` with the JSON, `404` if none is saved. |
| `PUT /api/sites/{site_id}/roi` | `editor` | Validate all fields and matching `site_id`, save atomically, return `200` with the JSON. Invalid body: `422`. |
| `DELETE /api/sites/{site_id}/roi` | `editor` | Remove the file; `204`, including when already absent. |

Every route declares its role. Authentication follows M1's existing shared-token
policy in `backend/app/auth.py`: with `QARAU_API_TOKEN` configured, missing or
incorrect Bearer credentials return `401`; without one, the explicitly existing
development mode permits requests. Individual editor/viewer enforcement awaits
the real identity/role provider. Invalid site paths return `400`; the body limit
is 64 KiB (`413` beyond it), unsupported methods return `405`, and storage failures
return `500`. Responses are uncached. Corrupt persisted JSON produces an error,
never an apparently absent region. The client sends the existing session Bearer
header and surfaces failures in translated viewer messages.

### Delivered implementation and activation

The backend half is task **A7a** (owner `dev_pipeline`, `backend/` only): the three
routes above with the existing `require_role` dependency, a Pydantic v2 model, the
atomic write and the path checks, with backend tests. Until A7a is merged the
production API has no ROI routes, and the viewer shows its translated load error
instead of an empty region.

`web/vite-dev-roi.ts`, mounted by the existing Vite catalog adapter, provides the
same file format and working disk persistence for local development. When
`QARAU_API_PROXY` forwards `/api` to FastAPI, the development adapter is disabled
and ROI requests go to the A7a routes. The static web build uses the same
`/api/sites/.../roi` client and cannot supply missing API routes itself.

### Finding and checking the tool

`/viewer/:runId` is routed by `web/src/main.tsx` to `ViewerPage`, which imports
`ViewerScreen`. Its horizontal **Viewer tools** toolbar has a text-labelled
**Region of interest** button. On real runs the ROI panel starts open, including
on `/viewer/notre-dame`; the demo keeps it closed until selected. No region is
invented when the site has none saved.

Select **Draw region**, then drag on the map. The viewer changes to top view,
shows a live drag rectangle, and displays the resulting neutral wireframe.
Enable **Hide points outside region**, select **Save**, and reload to check the
saved outline. **Clear** removes the site file. The ROI panel can be closed and
reopened using the text button.

The screenshot gate starts Vite from the task worktree with `--strictPort` and
reads the current source entry (`/src/main.tsx`), rather than `web/dist`. Its
plain URL capture shows the initial controls; a drawn-box capture additionally
requires the interaction above or an existing saved site ROI. The ROI Playwright
regression performs the interactions without taking screenshots.

### Saved Notre-Dame fixture for the tester

Prepare an isolated catalog explicitly; this command reads the sample catalog
and copies its viewer bundle without editing the source or writing under `data/`:

```sh
cd web
pnpm roi:prepare --source /path/to/read-only/runs --out /path/to/A7-scratch/notre-dame-roi-review
QARAU_RUNS_DIR=/path/to/A7-scratch/notre-dame-roi-review pnpm exec vite --port 5198 --strictPort
```

Open `http://localhost:5198/viewer/notre-dame?view=top&hideOutside=1`.
The ordinary site-ROI GET reads
`sites/notre-dame/roi.json` from this catalog, so the saved wireframe is present
before drawing. The explicit view parameters select top view and enable
**Hide points outside region** after the saved ROI loads, so the toggle and the
filtered geometry are already in the requested review state. The normal URL
retains its ordinary defaults. If no saved ROI exists, the parameters do not
create a box or claim that filtering is active. Stop the server after checking.
The application has no fixture fallback or automatic sample seeding.

The generated box uses the actual finite object's XY 2nd–98th percentile bounds
and the selected full Z extent plus 5% on each end. `created_by` is explicitly
`a7-visual-test-fixture`; `roi-test-fixture.json` records its purpose and point
counts. It is a visual-test artifact, not an engineer-approved selection.
The preparation script refuses existing destinations, overlapping source/output
catalogs, paths under `data/`, escaping asset paths and malformed geometry.

For this A7 worktree, the prepared catalog is
`/Users/izbassar/Documents/Startup/qarau/devteam/.state/scratch/A7/notre-dame-roi-review`.
It contains 15,239 real sample points: 14,134 inside the ROI and 1,105 outside.
The locally restored FastAPI serves its saved ROI, manifest and position buffer
with `200` responses. This API check uses TestClient and takes no screenshot.

**The existing screenshot command still hardcodes `QARAU_RUNS_DIR={data}/runs`.**
The orchestrator/tester must explicitly select the prepared catalog for a
saved-box capture; preparing it under scratch does not change the fixed gate's
input. This developer does not modify ownership or screenshot gate controls.

No dependencies or models were added. FastAPI and Pydantic are MIT; Python and
Node standard libraries and the installed Vite stack permit commercial use.
Vite's local `LICENSE.md` is MIT (its bundled permissive
MIT/BSD/ISC/Apache-2.0/CC0 components also permit commercial use).
