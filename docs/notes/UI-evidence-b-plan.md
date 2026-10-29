# UI-evidence-b

Continuation audit (2026-10-29): preserve the existing implementation and tests. Close the
remaining public-client contract gaps with listDecisions and a shared lib/me.ts getMe /
meOptions used by role resolution. Mark the evidence panel itself busy during identity
resolution, and add the missing translated comment placeholder. Verify these additions
with transport and pending/error tests, then run lint, typecheck, test and build. Existing
MIT dependencies permit commercial use; no dependencies, API writes or servers are needed.

Audit completed: listDecisions is the query's GET client; lib/me.ts exports getMe and
meOptions, and AuthProvider resolves roles through that shared query. The existing role
transport tests moved to me.test.ts with their assertions preserved and now check the
full identity fixture. The evidence panel itself tracks initial and repeated role loading
and clears aria-busy on success or failure; session tests assert both panel and log
readiness. The comment placeholder has en/ru/kk keys. No layout or design values changed.
Final pnpm lint, pnpm typecheck, pnpm test (48 files, 855 tests) and pnpm build pass;
git diff --check passes. The existing bundle-size advisory remains. No real decision was
posted, no server started, and no screenshot taken. Kerege owns e2e/screenshots; the runner
credential mismatch documented below remains outside the permitted change scope.

Add typed decision GET/POST calls using the session transport, an oldest-first append-only
log, and engineer/admin actions below the evidence. Require a comment for reject/reclass,
offer the API's class choices for reclass, and invalidate finding and decision queries after
a successful write. Keep the existing v8 components and tokens. Add all copy in en/ru/kk.

Continuation after round 3 and the dev merge: keep the existing log, form, status refresh,
translations and v8 layout. ME1 now supplies GET /api/me, so replace the previous
GET /api/users denial parsing with its explicit role. Unknown or failed responses remain
read-only with a visible message. Keep all backend files unchanged.

Remaining work: expose getDecisions and the API-shaped new_class input; keep the log section
mounted and aria-busy while decisions or identity load; test delayed success and terminal
errors, viewer/engineer roles through mocked /api/me, and a delayed finding refetch to prove
the displayed status comes from the server. Update the existing preview e2e's identity mock.

Role resolution is lazy: only an opened finding requests it, so offline demo views retain
their existing behavior. The context stays read-only while resolution is pending or fails.

Verify transport paths/bodies, chronological log order, absence of edit/delete controls,
required comments, literal reject comment, role gating, and status/log refresh using
fixtures and mocks. Run pnpm lint, typecheck, test and build. Kerege owns screenshots/e2e;
start no servers. No dependencies or models added. Existing React, TanStack Query, Vitest
and Testing Library packages declare MIT licences, permitting commercial use.

Completed: pnpm lint, pnpm typecheck, pnpm test (46 files, 839 tests), and pnpm build pass,
including noLiterals, noFixtureFindings and i18n keys. The production build emits its chunk
size advisory. The existing evidence preview e2e mock now supplies the role and empty decision
log and asserts the actions/comment field. E2e and screenshot verification remain with Kerege;
no server was started and no real decision was submitted.

Screenshot feedback follow-up: actions now precede the log, directly after the frames, as
requested to match the finding reference. Evidence heights and form spacing use existing
spacing tokens; the image and SVG share a fixed-ratio inner stage so the height cap does not
misalign the outline. Smaller phone frame heights leave room for the same controls. No
colors, fonts or tokens changed.

Added session integration coverage in en/ru/kk: the real AuthProvider consumes a mocked
identity response, sends the session header, displays all three actions and
the comment before the log, and shows no alert. Existing assertions are unchanged. The
existing preview e2e now additionally requires the comment, actions and empty log to be
fully inside the viewport without scrolling. Kerege runs that browser check and captures
screenshots; no local screenshots or server were started. Rechecked pnpm lint, typecheck,
test (47 files, 842 tests) and build successfully.

Continuation complete: role reads now use GET /api/me; getDecisions and postDecision expose
the requested API contract, including new_class. The log section remains mounted through
loading and errors and tracks initial and repeated identity/decision reads with aria-busy.
Tests cover mocked viewer/engineer identities, pending/error readiness, the absence of any
log controls, and the unchanged status until the delayed finding GET returns rejected.
The preview e2e mock now serves /api/me. Final pnpm lint (ESLint + Prettier), typecheck,
test (47 files, 853 tests) and build pass; the existing bundle-size advisory remains.
Installed React, TanStack Query, Testing Library and Vitest licence files were checked:
MIT permits commercial use. No new dependency, server, screenshot or real API write.
Kerege still owns browser e2e and screenshot verification.

Screenshot return investigation (2026-10-28): the recorded /api/me response is not a new
schema. Using the real CRUD/runs routers through FastAPI TestClient against the last
screenshot database opened read-only, the tester's configured password sends a Bearer
header, gets 200 from /api/runs, but gets 401 application/json from /api/me with
{"detail":"invalid or missing token"}. The seeded engineer gets 200 application/json with
id 2, name engineer, role engineer, organization {id: 1, name: dev}. No server was started,
no secret printed or saved, and no API write was made. FastAPI/SQLAlchemy are MIT and
httpx/python-dotenv BSD-3-Clause; installed licence metadata was checked for this probe.

Root cause: devteam/config.yaml screenshots.api seeds fixed per-user credentials, while
devteam/shoot.mjs signs in with the unrelated QARAU_TEST_PASSWORD from devteam/.env.
Open dev mode lets the existing /api/runs login check pass for that unknown credential;
ME1 correctly refuses it because there is no authenticated user. The parser and session
header are correct. Changing header syntax, accepting the 401 body as a role, or embedding
a seed credential in the web client cannot fix this correctly.

Added a recorded-response fixture (no credentials) and viewer-route regression coverage
through the real login/session/auth provider: the 401 remains read-only with an alert;
the actual seeded engineer response shows all three actions and the comment without an
alert at /viewer/notre-dame-p3e?finding=first. Previous assertions remain unchanged.
The outstanding fix is to align the screenshot tester credential with a seeded engineer
or admin in the runner configuration. devteam/config.yaml and devteam/.env are outside
this task's permitted edits; the former is explicitly protected. No protected files changed.
Verification after adding the recorded fixture and route regressions: pnpm lint (ESLint and
Prettier), pnpm typecheck, pnpm test (47 files, 855 tests), and pnpm build pass. The existing
bundle-size advisory remains. Screenshot acceptance is still blocked on the runner credential
alignment; this round does not claim a frontend parser change fixes the real 401 response.
