# 0034 — Event organizations and guest access (LaunchPad demo)

- Status: accepted for part 1 (schema and `create` CLI); part 2 (sign-in, sandbox, purge) adds to it
- Date: 2026-10-28
- Task: DEMO1 (owner 2026-10-28: risk high, guest access to a real system)

## Context

At the LaunchPad demo (2026-11-06) up to ~300 visitors should try Qarau on real processed runs
without an account. Until now only users created by an admin can sign in, and `decision` /
`audit_log` are append-only in every organization (migration 0001, ADR 0021). Guest data must be
removable after the event without opening a way to delete a real engineer's decision.

## Decision

**Event flag.** `organization.is_event` (NOT NULL, default false). Only
`python -m app.event create` sets it, at INSERT. A trigger rejects any UPDATE that changes it
(Postgres `qarau_event_flag_immutable()`, SQLite `RAISE(ABORT)`), so neither the API, a bug nor a
direct SQL session can turn a customer organization into one whose decisions can be deleted, or
clear the flag of an event. `POST /api/organizations` has no such field (an extra key is ignored;
tested).

**Codes.** `event_code` stores name, `code_sha256`, `expires_at` (UTC), `max_guests`. The code is
8 characters from `secrets.choice` over an alphabet without look-alikes (31⁸ ≈ 8.5·10¹¹); it is
printed once and never stored. A plain SHA-256 is enough because the code is random and short-lived
and guesses are rate-limited (part 2); a salted slow hash would force a scan of all codes per
attempt. `event_code_run` lists the real runs shared read-only; `create` refuses a run that does
not exist or is not `done`. No demo data is invented or seeded.

**Guest sandbox.** `guest` (organization, event code, created_at) is one guest's sandbox; a
composite foreign key ties it to its code's organization and a trigger refuses a guest or code
outside an event organization. `finding`, `decision` and `report` get a nullable `guest_id`
(foreign keys on Postgres; SQLite cannot add or drop a column with a foreign key without
rebuilding the table, which would drop the append-only triggers, so there it is a plain integer).

**Scope of the trigger exception.** The decision trigger becomes `qarau_decision_append_only()`:
UPDATE is always refused; DELETE passes only when the row's `guest_id` names a guest of an event
organization **and** the row's `user_id` is a user of that same organization. `guest_id` alone is
not enough, so a customer user's decision stays undeletable even if tagged with a guest id.
`audit_log` keeps the 0001 trigger unchanged in every organization: purge never deletes an audit
row, so a code use is logged against a user of the event organization that purge keeps (part 2).
Downgrade restores the 0001 trigger bodies.

**Rate limiting (part 2).** An in-process limiter (per IP and per code, our own code, no new
dependency). It holds only for a single API process; several workers or hosts would each count
separately. Acceptable for one demo server; move the counters to Valkey when the API scales out.

## Consequences

- Guest data lives in the same database as customer data, separated by organization and guest id;
  part 2 must scope every guest-reachable query (runs via `event_code_run`, findings, decisions,
  reports via `guest_id`) and must test cross-guest reads (404).
- The event organization's users and audit rows survive purge by design.
- Codes cannot be recovered; a lost code means a new event.
