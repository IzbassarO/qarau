# 0021 — Backend: per-user bearer tokens, append-only tables by trigger, chunked upload, SQLite for tests

- Status: accepted (dev team, B1)
- Date: 2026-10-22

## Context

Until B1 the API had one shared token and `require_role()` ignored its argument. The data model
(organizations, users, sites, inspections, runs, findings, decisions, audit log) needed a home,
the decision log must be impossible to edit (it is our future training data and the engineer's
record of responsibility), and the upload task (U2) needs a resumable protocol for multi-GB
videos over field connections. Tests must run in CI without a database server.

## Decision

**Auth.** `Authorization: Bearer <token>`. A user's token is stored as
`scrypt(token, per-row salt)` (stdlib `hashlib.scrypt`, n=2¹⁴, r=8, p=1; no extra library).
Roles are a ladder, viewer < engineer < admin: `require_role("engineer")` admits engineers and
admins. Missing or unknown token → 401, role too low → 403. Every route declares its role and
its OpenAPI description starts with `Role: <role>`. A verified token is cached in-process as
`sha256(token) → (user id, stored hash)` and re-checked against the stored hash on every use, so a
rotated secret is noticed and scrypt runs once per token per process. If the database is down or
not migrated, user tokens fail closed (401 with a warning in the log).

The shared `QARAU_API_TOKEN` stays and maps to a read-only (viewer) identity, so the web app's
sign-in against `/api/runs` keeps working. An **empty** shared token is the M1 open dev mode:
read routes need no token; write routes always need a user token. `python -m app.seed` creates
org `dev` with users `admin`, `engineer`, `viewer` from `QARAU_DEV_*_TOKEN` and runs only when
`QARAU_ENV=dev`. `POST /api/users` returns a generated token exactly once.

**Append-only.** `decision` and `audit_log` have no PUT/PATCH/DELETE handler; those methods are
registered explicitly and answer 405 so the refusal is visible in OpenAPI and tested. The database
refuses too: migration 0001 adds `BEFORE UPDATE OR DELETE` triggers (plpgsql `RAISE` on
Postgres, `RAISE(ABORT)` on SQLite), so no code path, ORM or raw SQL, can edit history. Every
write route inserts an `audit_log` row (who, when, route, object, action).

**Upload.** `POST /api/inspections/{id}/video {name, size}` creates a Run (its uuid is the
directory name under `QARAU_RUNS_DIR`) and an upload id. `PUT …/video/chunks?upload_id&offset`
appends the raw body to `<run>/video.<ext>.part`; the file size is the truth for `next_offset`.
A chunk already stored (offset + length ≤ next_offset) is acknowledged without writing, a chunk
past `next_offset` is 409 with the expected offset. `GET …/chunks` returns `next_offset` so a
client resumes after any failure. `POST …/video/complete {upload_id, sha256}` checks size and
sha256 and renames to `video.<ext>`; a mismatch is 422 and keeps the partial file. D1 moves the
finished file to S3.

**Tests.** The same models and migration run on Postgres and SQLite (portable types only:
`sa.JSON`, non-native enums, triggers written per dialect). Tests build a fresh database per
test with `alembic upgrade head` on a SQLite file; with `QARAU_TEST_DATABASE_URL` set the same
tests run on Postgres, which is how the migration and triggers were verified.

## Consequences

- No passwords, sessions or SSO yet (M5). Token rotation is "seed again" or a new user.
- Tenant isolation stays per schema/database (CLAUDE.md); routes do not filter by organization.
- The verified-token cache is per process; a revoked user must also be removed from the table
  (the cache re-checks the stored hash, so a deleted or rotated user is refused).
- One uploader per upload id is assumed (no lock on the `.part` file).
- Chunk PUTs are not audited one by one; `start` and `complete` are.
- Any later Postgres-only feature (PostGIS columns, pgvector) needs a SQLite fallback or those
  tests move to Postgres only.
