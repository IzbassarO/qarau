# REV1-api — Decisions: append-only confirm / reject / reclass: plan

Date: 2026-10-24. Scope: `backend/`, `docs/`. No UI (UI-evidence), no bulk decisions, no
notifications, no edit or undo of any kind.

## What exists

- B1 (migration 0001): table `decision` (`finding_id`, `user_id`, `verdict` confirm | reject |
  reclass, `comment`, `created_at`) with the append-only trigger (Postgres `qarau_append_only()`
  function, SQLite `RAISE(ABORT)` triggers) and the same guard on `audit_log`. PUT / PATCH /
  DELETE `/api/decisions/{id}` already answer 405 (`crud.py`).
- DEF1-api: `finding` rows with `status = "proposed"`, `class`, `severity`; `GET /api/findings/{id}`
  and the inspection list in `backend/app/findings.py`.
- Roles: `Viewer` / `Engineer` dependencies in `crud.py` (ADR 0021); `audit()` adds one
  `AuditLog` row per write.

## Steps

1. Migration `v0004_decision_verdicts.py`: add nullable `new_class` (String 64) and
   `new_severity` (same non-native enum as `finding.severity`) to `decision`. Plain
   `ADD COLUMN` / `DROP COLUMN`, never batch mode: SQLite batch mode recreates the table and
   would silently drop the append-only triggers. Downgrade removes the two columns.
2. `backend/app/decisions.py`: `DecisionIn` (verdict; comment required for reject and reclass;
   reclass needs `new_class` and/or `new_severity`; confirm and reject may not carry them),
   `DecisionOut`, `current_state(finding, decisions)`,
   `POST /api/findings/{id}/decisions` (engineer, 201, insert + one AuditLog row, no dedup) and
   `GET /api/findings/{id}/decisions` (viewer, oldest first). No PUT / PATCH / DELETE handler,
   so FastAPI answers 405.
3. `findings.py`: the response `status`, `class`, `severity` come from `current_state` over the
   finding's decisions; the `finding` row keeps the detection values. `FindingClass` moves to
   `models.py` so both modules use it without a circular import.
4. Tests `backend/tests/test_decisions_api.py` (literal expectations), `test_openapi.py`,
   `test_roles.py` (route lists), `test_migrations.py` (0004 round trip keeps the trigger).
   Run the suite on SQLite and on the dev Postgres (`QARAU_TEST_DATABASE_URL`, a separate
   `qarau_test` database) so the Postgres trigger is exercised.
5. Docs: `docs/architecture.md` (decision log API), ADR 0025 (state derived in the response).

## Decisions taken here

- Effective state is computed by replaying the decisions oldest first: `reject` → `rejected`;
  `confirm` → `confirmed`; `reclass` → `confirmed` with the class and/or severity it carries
  (a field it does not carry keeps its previous effective value). Nothing is written back to
  `finding` or to earlier decisions.
- `created_at` and the user come from the server and the session; the request body cannot set
  them.
- Licences: FastAPI, Pydantic, SQLAlchemy, Alembic, pytest, ruff, mypy (MIT); PostgreSQL
  (PostgreSQL Licence); psycopg (LGPL-3.0, unmodified library, already in the repo). No model,
  no new dependency.
