# 0034 — Event organizations and guest access (LaunchPad demo)

- Status: accepted (part 1: schema and `create` CLI; part 2: sign-in, sandbox, purge)
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
row. Downgrade restores the 0001 trigger bodies.

**Guest user (part 2).** `create` also inserts one user with role `guest` in the event
organization. It never gets a token (its secret is random hex no scrypt output matches, and
`find_user` skips the guest role). It is the `user_id` of every guest decision (which is what lets
the delete trigger accept them at purge) and `created_by` of guest reports, and the user of the
audit rows of guest writes. Purge keeps it, so no audit row ever points at a deleted user.

**Guest session (part 2, migration 0008).** `POST /api/auth/guest {code}` inserts a `guest` row
holding the SHA-256 of a new `secrets.token_urlsafe(32)` bearer token; the row is the session,
checked before the (slow, scrypt) user lookup and valid until the code's `expires_at`. Purge
deletes the row and with it the session. `max_guests` is counted under a process lock.

**Code-use audit (part 2, migration 0008).** Every attempt writes one `audit_log` row (action
`event_code.use`, `object_id` = event name or null, `detail` JSON `{result, ip}`). An unknown or
rate-limited code belongs to no event and no user, so `audit_log.user_id` becomes nullable (an
anonymous request) instead of inventing a system user. On SQLite this rebuilds the table, so the
migration creates the 0001 append-only triggers again (tested); on Postgres `ALTER` keeps them.
Downgrade fails while an anonymous row exists: the log is never edited to make it pass. A flood of
refused attempts adds rows that can never be deleted; the per-IP limit bounds the rate.

**Guest role and sandbox (part 2).** `guest` ranks below viewer and is deny-by-default:
`require_role` answers 403 to a guest unless the route is declared with `guests=True`. Only the
routes a guest needs are (view shared runs and their inspections' coverage, mark a finding,
decide, get the PDF), and each scopes the guest: runs by `event_code_run` (else 404), inspections
only when **every** run of the inspection is shared (so its findings, coverage and PDF show
nothing of an unshared run), findings and decisions by `guest_id` (another guest's → 404). Users
read only rows with `guest_id` null; the PDF loader takes the caller's guest id and shows only
that sandbox. New routes stay closed to guests unless someone opts in, which is the safe failure.

**Rate limiting (part 2).** An in-process sliding window (`app/ratelimit.py`, our own code, no
new dependency), per client IP and per submitted code, run before the code lookup; limits in
settings (`QARAU_GUEST_RATE_PER_IP` 10/min, `QARAU_GUEST_RATE_PER_CODE` 30/min). It holds only
for a single API process; several workers or hosts would each count separately. Acceptable for
one demo server; move the counters to Valkey when the API scales out. Two deploy notes for D2:
the client IP is the proxy's unless uvicorn trusts `X-Forwarded-For` (`--proxy-headers
--forwarded-allow-ips`), and at a venue many visitors share one Wi-Fi IP, so 10/min per IP and
30/min per code throttle a crowd (300 guests need ≥ 10 minutes); raise both for the event.

**Open dev mode.** With `QARAU_API_TOKEN` empty every read route is public, guest or not; a demo
server must set it.

## Consequences

- Guest data lives in the same database as customer data, separated by organization and guest id;
  part 2 must scope every guest-reachable query (runs via `event_code_run`, findings, decisions,
  reports via `guest_id`) and must test cross-guest reads (404).
- The event organization's users and audit rows survive purge by design.
- `audit_log.user_id` may be null; readers (`GET /api/audit-log`) show such rows as anonymous.
- Codes cannot be recovered; a lost code means a new event.
