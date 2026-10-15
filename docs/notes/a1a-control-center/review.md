# A1a: sites and inspections control center

The old runs table is replaced with a dark control center: collapsible site groups,
active inspection navigation, a New inspection dialog, and a selected inspection's
video frames, existing 3D viewer and pipeline steps. The sidebar collapses below 900 px.
All added interface and accessibility text is translated into English, Russian and
Kazakh. Standalone `/viewer/:runId` links still work; `/runs` redirects to `/`.

`GET /api/runs` remains the data source. Current responses lack site/date/status
metadata: missing sites appear under a translated Unassigned group, and missing
metadata is explicitly unavailable. Timing entries never imply successful analysis.
HTTP/network errors show a translated banner with retry; 401 explains authentication
without adding a local login. Demo geometry and illustrated frames appear only on
`/inspections/demo`, with a persistent Demo badge and an explicit synthetic-data notice.

The backend has no upload endpoint. The dialog validates non-empty video MIME types
and visibly explains that uploading cannot start. The requested dev_pipeline contract
is recorded in [the plan](../a1a-control-center-plan.md). No video is sent to an invented
endpoint.

## Validation

- `cd web && pnpm lint && pnpm typecheck`: passed.
- `cd web && pnpm test && pnpm build`: passed (31 tests, 6 files).
- Frozen-lockfile offline installation: passed using local cached packages.
- `git diff --check`: passed.
- Browser verification/screenshots: blocked by sandbox process/socket restrictions.
  Vite returns `listen EPERM` on IPv4 and IPv6; independent Chromium launch terminates.
  Screenshots have not been captured or visually reviewed.

To produce the four requested screenshots in a browser-enabled environment:

```sh
cd web
QARAU_SCREENSHOT_DIR=../docs/notes/a1a-control-center pnpm e2e control-center.spec.ts
```

This scenario captures `home-en.png`, `home-ru.png`, `demo-en.png`, `demo-ru.png` and checks
mobile navigation at 390 px. Home deliberately uses an intercepted 503 response to
verify the real error state; the demo loads its own assets without the backend.
Attach these four images to the orchestrator's PR after inspecting them. They are
pending artifacts, not existing attachments.

Added test dependencies @testing-library/react, @testing-library/dom and jsdom are MIT;
existing runtime libraries are MIT, Playwright is Apache-2.0 and IBM Plex is OFL-1.1.
No models or paid services used. Changes are limited to `web/` and `docs/`;
`docs/notes/progress.md` is intentionally untouched under the task's final rules.
