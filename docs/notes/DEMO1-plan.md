# DEMO1 — Event codes: guest access for the LaunchPad demo (plan)

Backend only (`backend/`, `docs/`). Decisions: ADR 0034. Two parts; the tester accepts each.

## Part 1 — event schema and `create` CLI (this change)

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

## Part 2 — guest sign-in, sandbox, purge

- Guest event user: one user per event organization (made by `create` or lazily at first
  sign-in) is the `user_id` of guest decisions and of the code-use audit rows, so purge never
  deletes an audit row and the delete trigger's "same organization" check holds.
- `POST /api/guest/session {code}`: unknown or expired code → 401, full (`max_guests`) → 403 or
  401; rate limits per IP and per code → 429 (in-process, single-process limit: ADR 0034); every
  use (success or failure with a known code) in `audit_log`. Returns a guest token (stored hashed).
- `guest` role: read only runs in `event_code_run` (others 404), no inspections of other
  organizations; mark findings, decide, get the PDF in its own sandbox (`guest_id`); other
  guests' rows 404; upload / processing / admin routes 403.
- `app.event purge --name`: deletes decisions, observations, findings, reports and guests with
  that event's guest ids, only in an organization with `is_event`.
- Tests: expired code 401, A reads B's finding 404, guest upload 403, rate limit 429, after purge
  no guest rows, each code use in the audit log, guest 404 on an unshared run.
