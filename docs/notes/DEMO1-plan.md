# DEMO1 — Event codes: guest access for the LaunchPad demo (plan)

Backend only (`backend/`, `docs/`). Decisions: ADR 0034. Two parts; the tester accepts each.

## Part 1 — event schema and `create` CLI (accepted)

1. Migration `v0007_event_guests.py`: `organization.is_event` (NOT NULL, default false) and a
   trigger rejecting any UPDATE that changes it; tables `event_code` (name, `code_sha256`,
   `expires_at`, `max_guests`), `event_code_run`, `guest` (trigger: only in an event org;
   composite FK to its code's org); nullable `guest_id` on `finding`, `decision`, `report`.
2. Decision trigger reworked: UPDATE always refused; DELETE only for a guest's decision whose
   user is in that guest's event organization. `audit_log` unchanged. Downgrade restores 0001.
3. `app/models.py`: new columns and tables. Only `app.event create` sets `is_event`.
4. `app/event.py`: `create --name --expires (ISO 8601 with offset) --max-guests --runs a,b`
   validates runs (exist, `done`), expiry (future, with offset), names (unused), writes org +
   code hash + shared runs in one transaction, prints the 8-character code. Errors exit 2, no
   rows. `purge` is a stub (exit 2) until part 2.
5. Tests: `test_event_flag.py` (flag set/clear refused, API cannot set it, guests/codes only in
   event orgs, normal decision UPDATE/DELETE refused, guest-tagged decision of a normal user not
   deletable, guest decision deletable but not updatable, audit_log append-only in an event org),
   `test_event_cli.py`, `test_migrations.py` (0006 → 0007 with data, downgrade, upgrade).

## Part 2 — guest sign-in, sandbox, purge (this change)

1. Migration `v0008_guest_sessions.py`: `guest.token_sha256` (unique index; the row is the
   session), `audit_log.user_id` nullable and `audit_log.detail` JSON (an unknown code has no
   event and no user). SQLite rebuilds `audit_log`, so the 0001 triggers are created again.
2. `create` also makes the event's guest user (role `guest`, no token): `user_id` of guest
   decisions and reports, so the delete trigger accepts them at purge and audit rows of guest
   writes point at a user purge keeps.
3. `app/auth.py`: role `guest` (below viewer, deny-by-default: only `require_role(...,
   guests=True)` routes let it in), guest-token lookup before the scrypt user scan, scoping
   helpers (`check_run`, `check_inspection`, `own`), `POST /api/auth/guest` (public sign-in) and
   `GET /api/auth/guest`. `app/ratelimit.py`: in-process sliding window; limits in settings.
4. Scoped routes: runs (list, manifest, files), inspection, coverage, findings (list, get,
   create), decisions (list, create), report PDF; the pipeline report loader takes `guest_id`.
5. `app.event purge`: one transaction, only in an `is_event` organization; keeps event code,
   shared runs, guest user and every audit row; prints counts.
6. Tests: `test_guest_api.py` (401 expired/unknown, 403 full, 429 per IP and per code with
   Retry-After, exact audit rows, upload/processing/admin 403, unlisted run and other inspections
   404/403, A↔B 404, users never see guest rows, guest PDF only its sandbox),
   `test_event_purge.py`, `test_migrations.py` (0008), pipeline `test_report.py` (guest filter).
