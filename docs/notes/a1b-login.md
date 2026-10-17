# A1b — local shell login

## Plan

1. Read the backend contract and design tokens; check existing library licences.
2. Add session-only authentication, a context, a route guard and a localized dark login form.
3. Protect shell routes and production viewer routes; add sign-out and authenticated API requests.
4. Cover login, rejection, logout, session restoration and route access with tests.
5. Run lint, TypeScript, Vitest and the production build; verify browser behaviour without screenshots.

## Contract and assumptions for the PR

- `backend/app/auth.py` accepts `Authorization: Bearer <operator-supplied token>`.
  There is no login endpoint or username/password account database. `GET /api/runs`
  requires the `viewer` role and is the login probe. Only HTTP 200 signs in;
  401/403 reject the credentials, while other failures show a connection error.
- The required password field supplies the existing API token. The required username
  is a local form label only: the backend cannot validate it, and the app does not
  send or persist it. This does not establish an individual identity or audit actor.
  Credentials are never embedded in source, environment variables exposed to Vite,
  URLs or persistent browser storage. The validated Bearer header lives only in
  `sessionStorage` for the current tab. Logout also clears the query cache.
- `/`, `/inspections/demo` and `/inspections/:id` require a local session. The
  standalone `/viewer/:runId` route deliberately stays accessible without login
  only when `import.meta.env.DEV` is true, including `/viewer/demo`, so the gate
  tester can inspect benchmark output. Production builds guard every viewer route.
  This exception does not bypass the backend's role check for real run data.
- With the existing unauthenticated Vite runs middleware, or a backend with no API
  token configured, the probe accepts any nonempty input. Deployments must configure
  `QARAU_API_TOKEN` on the backend; this task does not change backend defaults.
- `processing.ts` is a pure adapter with no fetch helper. Inspection requests and
  the viewer's existing manifest/buffer fetches use the shared auth header.
  Protected video frames are fetched with that header into temporary blob URLs,
  revoked when the frame changes or unmounts. Public dev and demo assets keep
  normal image URLs.

## Licences

No new dependencies or models. Inspected installed package metadata and licence
files: React/React DOM, React Router, TanStack Query, Vite, Vitest and Testing Library
are MIT; TypeScript and the existing Playwright test runner are Apache-2.0.
The installed Gitleaks verifier is MIT. These licences permit commercial use with
their existing notice obligations.

## Verification

- Passed `pnpm lint`, `pnpm typecheck`, `pnpm test` and `pnpm build` in `web/`.
- Also passed the requested `npm run lint`, `npx tsc --noEmit`, `npx vitest run`
  and `npm run build`. Vitest: **19 files, 295 tests passed**, including i18n key
  parity, login success/rejection/logout, pending requests, storage failure,
  signed-out/signed-in shell, requested-route preservation, query-cache cleanup,
  production/dev guards and authenticated manifests, arrays and frame images.
- Gitleaks scans of `web/src` and `web/e2e` found no secrets; `git diff --check`
  passed. Only `web/` and this note changed; no dependencies were added.
- Added browser checks in `web/e2e/login.spec.ts` for the three localized dark
  forms, rejected/accepted login, reload, logout, narrow layouts and the public
  black-background dev point viewer. Updated existing control-center tests to
  start with an authenticated test session.
- Browser execution is **unverified in this sandbox**: Playwright could not start
  Vite because listening on localhost port 5199 returned `listen EPERM` for both
  IPv6 and IPv4. No screenshots were taken. The gate tester must run these browser
  checks and its visual checks in an environment that allows a dev server.
- Production build succeeds with Vite's existing large-chunk warning for the
  viewer bundle. No backend, pipeline, benchmark data or backend defaults changed.
