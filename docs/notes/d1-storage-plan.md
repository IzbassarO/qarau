# D1 — Storage layout on S3; `data/runs` is a cache: plan

Date: 2026-10-23. Scope: `pipeline/`, `backend/`, `infra/`, `docs/`. Not here: lifecycle rules,
multi-region, one bucket per customer (M5), any UI.

## Goal

S3 (Versity in dev, ADR 0006; the provider's S3 in the cloud, same code) becomes the system of
record for every run artefact. `data/runs/<run>` is only a local cache: a step downloads what it
lacks and uploads what it made, the API serves a file from the cache when it has it and from S3
otherwise. `rm -rf data/runs/<run>` loses nothing. With `QARAU_S3_BUCKET` unset nothing changes:
local files only, as today (the benchmark and the existing tests run that way).

## Steps

1. **Storage module** `pipeline/qarau_pipeline/storage.py` (boto3, Apache-2.0): a `Storage`
   protocol (`put`, `get`, `exists`, `list`, `delete`, `stream`, `signed_url`), `S3Storage` on
   boto3 (path-style addressing, SigV4, so the same client talks to Versity and to S3),
   `storage_from_env()` (`QARAU_S3_ENDPOINT/BUCKET/ACCESS_KEY/SECRET_KEY/REGION`; `None` when
   the bucket is unset), and the layout helper `artifact_key(org, site, inspection, kind, rel)`
   with `kind` in `video | frames | sparse | dense | bundle`. `put` refuses to overwrite a key
   under `video/`. Runs outside the catalog (CLI, benchmark) use org `_local`, site `_local`,
   inspection `<run dir name>`.
2. **Cache behaviour** (`workflow.py`, `layout.py`): `StepContext` gains `storage` and `prefix`
   (`<org>/<site>/<inspection>`). `run_step` pulls, before a step, the kinds it reads and its own
   kind when the local dir has no done marker (so a wiped cache is refilled, not recomputed), and
   pushes the step's kinds after its marker is written (`frames -> frames/`, `sparse -> sparse/`,
   `export -> bundle/`, `dense -> dense/ + bundle/`, `probe -> video/` once). `layout.KIND_DIRS`
   maps kinds to run sub-dirs and is the only place the backend and the pipeline share for keys.
   `step` gets `--prefix`; the backend runner passes the run's prefix on the command line, so it
   survives a wiped cache. The upload's `complete` puts the video at `.../video/<filename>`.
3. **Serving** (`backend/app/runs.py`, `settings.py`, `storage.py`): `QARAU_FILES_MODE = stream
   (default) | redirect`, `QARAU_SIGNED_URL_TTL_S = 300`. Manifest and `files/{path}` keep paths,
   roles and shapes: local hit → disk as today; miss → stream from S3, or 302 to a signed URL in
   redirect mode (needs CORS on the bucket, hence not the default). Traversal stays rejected: a
   path is mapped to a key only through `layout.KIND_DIRS` and never with `.` / `..` / empty parts.
4. **Config and docs**: `infra/.env.example` (every `QARAU_S3_*` and `QARAU_FILES_MODE`), worker
   env wiring in `infra/docker-compose.yml`, a storage section in `docs/architecture.md`
   (layout, immutable vs rebuilt kinds, the cache), ADR 0024.
5. **Tests** (moto, Apache-2.0): `pipeline/tests/test_storage.py` (round trip, sorted list,
   signed URL, `artifact_key`, bad kind, video overwrite refused), `test_workflow.py` (S1 run on a
   synthetic video fills the five prefixes; a deleted frames dir is re-downloaded; a wiped run is
   restored, not recomputed; no bucket → boto3 untouched), `backend/tests/test_storage_routes.py`
   (upload key, manifest after `rmtree`, redirect 302 with signature, 401).
6. **Real data**: S1 on `notre_dame_oubiels.webm` against the local Versity, listing in
   `docs/notes/d1-s3-listing.md`, `rm -rf` of the cache dir and the viewer routes still 200;
   quick benchmark with storage unset.
