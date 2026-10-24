# 0025 — Decision log: insert-only rows, finding state derived in the response

- Status: accepted (dev team, REV1-api)
- Date: 2026-10-24

## Context

The trust rules make the decision log (who confirmed or rejected which finding, when, why) our
future training data: never lost, never edited. B1 created the append-only `decision` table
(migration 0001: a trigger refuses UPDATE and DELETE on Postgres and SQLite) and DEF1-api creates
findings with `status = proposed`. Nothing recorded a verdict yet, and a `reclass` verdict had
nowhere to put the new class or severity.

## Decision

1. **Verdicts are rows, never fields.** `POST /api/findings/{id}/decisions` (engineer) inserts one
   `decision` row and one `audit_log` row (`decision.create`) in one transaction. There is no
   dedup (two identical POSTs are two rows: both happened), no PUT / PATCH / DELETE route (405) and
   the database trigger stays as the last line of defence. The user comes from the session and
   `created_at` from the server; the body cannot set them.
2. **`reclass` carries `new_class` and/or `new_severity`** (migration 0004, two nullable columns,
   plain `ADD COLUMN`; batch mode would recreate the table and silently drop the trigger on
   SQLite). The body is validated as a whole: `reject` and `reclass` need a comment, `reclass`
   needs at least one new field, `confirm` and `reject` may not carry one. The finding class
   vocabulary (`FindingClass`) lives in `models.py` so findings and decisions share it.
3. **The finding's state is derived, not stored.** `finding.status`, `class` and `severity` keep
   the proposed values; every finding response replays the decisions oldest first
   (`derive_finding_state` in `app/decisions.py`): the status follows the latest decision
   (`reject` → `rejected`, `confirm` / `reclass` → `confirmed`), a `reclass` sets the field(s) it
   carries and later decisions keep them, no decision → proposed. One helper, used wherever a
   finding is serialized.

Alternatives rejected: writing the derived status back onto `finding` (two sources of truth; a
missed write-back would show a stale status, and a rejected row that is later confirmed would
need an UPDATE path we want nowhere near this data); taking only the latest decision for class and
severity (a `confirm` after a `reclass` would silently revert the class).

## Consequences

- Reading a finding costs one extra query per finding (its decisions). Findings have a handful of
  decisions each; a join or cache comes when the inspection list grows past that.
- Undo is a new decision with a comment, never an edit; bulk decisions, notifications and the UI
  are separate tasks (UI-evidence).
- Tests run the same suite on SQLite and, with `QARAU_TEST_DATABASE_URL`, on Postgres, so both
  triggers are exercised against raw `UPDATE` / `DELETE`.
- Licences: FastAPI, Pydantic, SQLAlchemy, Alembic, pytest (MIT), PostgreSQL (PostgreSQL
  Licence), psycopg (LGPL-3.0, unmodified). No new dependency, no model.
