# U2 — adapt the web uploader to B1

- Status: accepted
- Date: 2026-10-23

B1 requires a SHA-256 checksum on completion, but browser Web Crypto cannot hash a
stream. Reading a multi-GB file into one ArrayBuffer would exhaust browser memory.
Use a small first-party incremental SHA-256 implementation, verified against fixed
standard vectors, reading at most one 4 MiB slice at a time. This is transfer integrity
required by the API, not video processing. No dependency is added.

B1 currently has no upload-size maximum or MIME restriction. Retain the existing
non-empty `video/*` validation and impose a documented frontend cap of 10 GiB. Server
413/415 responses still take precedence and map to translated validation messages.
This cap can be replaced by server-advertised capabilities when available.

B1 completes a Run while its Inspection remains `created`. For that legacy state only,
read the inspection's runs and show its sole run's status from the API. S1 inspection
statuses take precedence, including unfamiliar values, shown verbatim with neutral unknown styling.
The same run association locates the existing viewer bundle. Never show an uploaded
video as processed or request a viewer bundle before processing is done.

Retry keeps the upload id in the open dialog and re-reads the server offset; it does
not create a second inspection or a second run. Cancel aborts local transfer only:
there is no B1 endpoint for deleting partial uploads. Lost responses to session creation
cannot be safely retried automatically because B1 has no idempotency key.
If completion succeeded but its response was lost, retry confirms the matching run's
uploaded/processing/terminal API state before attempting completion again. Another
run's completion never counts as confirmation.

The orchestrator serves screenshots with Vite's read-only run catalog, without
FastAPI. The production client therefore distinguishes missing CRUD capabilities
from transport/auth/server failures. Only an absent inspection endpoint permits
reading the legacy run list. Unreported run statuses remain unknown, and a legacy
viewer export remains viewable without claiming successful pipeline stages.

Missing site management is an availability state in the dialog, with upload disabled
and a translated explanation. It is not a connection failure. No fake sites or
fallback ids are supplied: a numeric id must come from the actual site API before a
write is enabled. Authentication failures and 5xx responses retain visible errors
and Retry. No changes to backend routes or the orchestrator configuration are needed.
