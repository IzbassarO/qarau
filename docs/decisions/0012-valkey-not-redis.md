# 0012 — Valkey, not Redis, for the job queue

- Status: accepted (owner, 2026-10-15)
- Date: 2026-10-15

## Context

The pipeline runs as jobs from a durable queue (task S1). CLAUDE.md named Redis + RQ or
Celery. Redis is no longer open source under a permissive licence: since 7.4 it is
RSALv2 / SSPLv1, and since 8.0 AGPLv3 is offered as a third option. On-prem deliveries
ship the queue server to customers (state companies that cannot use our cloud), so its
licence must be permissive and carry no network-use or "service" obligations.

Valkey is the Linux Foundation fork of Redis 7.2.4 (the last BSD release), BSD-3-Clause,
protocol- and command-compatible, so Redis clients and RQ work with it unchanged.

## Decision

- The queue server is **Valkey** (BSD-3-Clause), in dev (Docker Compose), in the cloud and
  on-prem. Redis is not shipped or required.
- The task runner is **RQ** (BSD-2-Clause) over the `redis-py` client (MIT), which speaks the
  same protocol. Temporal later, only if RQ falls short.
- Our code never depends on Redis-only features added after 7.2 (e.g. Redis 8 modules).

## Consequences

- No licence obligations beyond BSD notices in on-prem installs.
- A managed cloud queue must be Valkey-compatible (e.g. a Valkey service), not a
  Redis-licensed one, unless a new decision says otherwise.
- If Valkey's licence ever changes, this decision is revisited before upgrading.
