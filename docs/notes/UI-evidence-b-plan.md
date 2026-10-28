# UI-evidence-b

Add typed decision GET/POST calls using the session transport, an oldest-first append-only
log, and engineer/admin actions below the evidence. Require a comment for reject/reclass,
offer the API's class choices for reclass, and invalidate finding and decision queries after
a successful write. Keep the existing v8 components and tokens. Add all copy in en/ru/kk.

The current auth context has no role and the backend has no identity endpoint. Within this
web-only task, resolve the session role with the existing read-only GET /api/users: 200
identifies admin; the documented auth dependency's 403 detail explicitly identifies viewer
or engineer. Unknown responses fail closed with a visible message. Never infer a role from
the entered username or bearer value. A dedicated identity endpoint is a future backend
improvement; no backend changes are made here.

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
