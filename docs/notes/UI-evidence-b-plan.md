# UI-evidence-b

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
