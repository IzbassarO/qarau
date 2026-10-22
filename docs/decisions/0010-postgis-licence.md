# 0010 — PostGIS: a separate, unmodified database server

- Status: accepted (owner, 2026-10-15)
- Date: 2026-10-15

## Context

PostGIS is GPL-2.0. Our code talks to it only over the PostgreSQL protocol (SQLAlchemy,
SQL), so it is a separate program, not a library linked into Qarau. The question was what
on-prem installs that ship it must do.

## Decision

- PostGIS runs only as part of a **separate, unmodified** PostgreSQL server (today
  `pgvector/pgvector:0.8.0-pg16` + PostGIS from the distribution's packages, `infra/`).
- We never patch, fork or statically bundle PostGIS, and never copy its code into ours.
- Our code reaches it only through SQL. Nothing GPL is linked into the API, pipeline or apps.

## Consequences

- Qarau's own code keeps its licence; GPL obligations cover only the PostGIS server we
  pass on.
- On-prem installs that ship the database image must also pass on PostGIS's licence text
  and offer its source (upstream source of the exact packaged version). The on-prem
  installer (M5) includes that notice.
- Any change to PostGIS itself (a patch, a custom build) needs a new decision first.
