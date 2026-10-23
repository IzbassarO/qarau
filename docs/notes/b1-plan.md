# B1 — Backend data model, roles and CRUD: plan

Date: 2026-10-22. Scope: `backend/`, `infra/`, `docs/` only. No queue (S1), no S3 (D1), no UI.

## Steps

1. **Dependencies.** `alembic` added to `backend/pyproject.toml` (MIT). SQLAlchemy 2, psycopg 3,
   FastAPI, Pydantic already present. Hashing: stdlib `hashlib.scrypt`, no new library.
2. **Models** (`backend/app/models.py`, one module): Organization, User (role, scrypt hash +
   per-row salt), Site, Inspection, Run (string uuid id = run directory name), Finding,
   Observation, Decision, ScaleReference, AuditLog, VideoUpload. `sa.JSON` and non-native enums so
   the same models run on Postgres and SQLite.
3. **Alembic** (`backend/alembic.ini`, `backend/alembic/`): URL from `QARAU_DATABASE_URL`
   (falls back to `DATABASE_URL`). Migration 0001 creates every table and the append-only
   triggers on `decision` and `audit_log`: plpgsql `RAISE` on Postgres, `RAISE(ABORT)` on SQLite.
4. **Auth** (`backend/app/auth.py`): `Authorization: Bearer <token>`; the token is checked
   against each user's scrypt hash (verified tokens cached in-process). Hierarchy
   viewer < engineer < admin. 401 unknown/missing, 403 too low. The shared `QARAU_API_TOKEN`
   maps to a read-only identity; an empty shared token keeps `/api/runs` open in dev exactly as
   today (reads only). `python -m app.seed` creates org "dev" + users admin/engineer/viewer from
   `QARAU_DEV_*_TOKEN`, only when `QARAU_ENV=dev`.
5. **Routes** (`backend/app/crud.py`, `backend/app/upload.py`): organizations, sites,
   inspections (+ runs by inspection), users, audit-log, explicit 405 stubs for
   PUT/PATCH/DELETE on decisions and audit-log, and the resumable video upload
   (start / PUT chunk / GET next offset / complete with sha256). Every write inserts an
   AuditLog row. Every operation description starts with `Role: <role>`.
6. **Tests** (`backend/tests/`): conftest builds a fresh DB per test (SQLite file by default,
   Postgres when `QARAU_TEST_DATABASE_URL` is set) via `alembic upgrade head`, seeds users with
   literal tokens. `test_migrations.py`, `test_roles.py`, `test_upload.py`, `test_openapi.py`.
   `test_api.py` untouched.
7. **Verify:** ruff, mypy, pytest in `backend/`; migration + tests against the compose Postgres
   if Docker is allowed in the sandbox.
8. **Docs:** `docs/architecture.md` (data model, roles, upload protocol), ADR 0021.

## Out of scope / deferred

- Findings and decisions CRUD (DEF1-api, REV1-api); S3 (D1); job queue (S1).
- Per-organization row scoping: tenants are isolated per schema/database (CLAUDE.md), not by
  row filters here.
- Chunk PUTs are not audited one by one (start and complete are).
