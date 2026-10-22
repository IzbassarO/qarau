# A3: Processing status view

## Plan

1. Read the run API implementation without editing backend files, the shell/data types,
   existing tests, and the relevant control-center and viewer design specifications.
2. Add typed, ordered processing steps and a resilient API mapper in `web/src/lib/processing.ts`.
   Preserve unfamiliar stages/statuses visibly, normalize pipeline failures, mark downstream
   stages as not run, and map error codes to localized explanations and recovery advice.
3. Add an accessible vertical processing list inside the selected inspection, with state
   icons, elapsed durations, running animation, and expanded failure explanations.
4. Make the default demo inspection show successful ingest/frames, a reconstruction
   failure (`too_few_registered_frames`), and export not run on `/` immediately.
5. Add matching English, Russian, and Kazakh translations and focused mapper/component
   tests covering every state, unfamiliar API values, fallback errors, and downstream skips.
6. Run `pnpm lint`, `pnpm typecheck`, `pnpm test`, and `pnpm build` in `web/`. Start
   the dev server and inspect `/` in a browser without taking screenshots. Check the
   final diff stays within `web/` and `docs/notes/`.

## Constraints and decisions

- Change only `web/` and `docs/notes/`; do not edit `docs/notes/progress.md` or backend files.
- Do not create branches, commit, push, write secrets, or write under `data/`.
- Reuse existing dependencies and CSS tokens; introduce no model or library. The existing
  React 19, Vite, Tailwind, TanStack Query, and Vitest/Testing Library dependencies are MIT
  as identified by the task; verify installed package licence metadata before use.
- Unknown API information remains visible. Failed stages explain the problem and the
  next action in all three supported languages; raw error details never replace advice.


## Implementation and verification

- The backend run list currently returns `run_id`, `source`, and `stats` only. The mapper
  accepts optional stage records with the existing pipeline `StageResult` fields:
  `name`, `status` (`ok`/`failed`), `started_at`, `finished_at`, `duration_s`, and `error`.
  It also accepts the five UI states, `queued`/`completed` aliases, and structured error
  codes/details. Timing-only records remain unknown instead of implying success.
- Missing or pending stages after a known failure become not run. Explicit reported
  results and unfamiliar states remain visible, including future stages and duplicate
  records. Technical error details are available on demand beneath localized advice.
- `/` selects the clearly labelled demo and highlights it in the sidebar. Real inspection
  routes continue to use the API and never substitute demo data. The processing panel
  precedes the map so the failure explanation is immediately accessible.
- Licence metadata checked locally: React, React DOM, Vite, Tailwind, TanStack Query,
  Vitest, and Testing Library are MIT. Existing Playwright is Apache-2.0. These licences
  permit commercial use. No dependencies, fonts, models, or services were added.
- Passed: `pnpm lint`, `pnpm typecheck`, `pnpm test` (13 files, 205 tests), and `pnpm build`.
  The build reports the existing large viewer chunk warning; it succeeds.
- Browser verification was attempted without screenshots but is blocked by the execution
  sandbox: Vite cannot bind `127.0.0.1:5199` (`listen EPERM`). A separate attempt to load
  the built page through Playwright request interception (without a listening server)
  also fails because macOS denies Chromium's Mach-port registration. No page could be
  opened in this environment. The control-center browser tests now assert the default
  demo states and advice in EN/RU/KK without taking screenshots; they still require a
  browser-capable environment to execute.
