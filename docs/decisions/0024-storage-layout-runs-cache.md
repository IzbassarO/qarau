# 0024 — Storage layout on S3: one prefix per inspection, five kinds; `data/runs` is a cache; stream by default

- Status: accepted (dev team, D1)
- Date: 2026-10-23

## Context

Until D1 every artefact of a run lived only on the local disk under `QARAU_RUNS_DIR` and was
served as files by `backend/app/runs.py`. The compose stack already ran Versity S3 Gateway (ADR
0006) but nothing wrote to it. A product that runs the same containers in the cloud, on our servers
and on a customer's server (CLAUDE.md) needs one place of record that is not a worker's disk: a
worker can be replaced, the API can run on another machine than the worker, and the evidence
frames a finding links to must outlive any cache. B1's upload and S1's steps (ADR 0022) are the
only writers; the viewer bundle (ADR 0003) is the only thing the web app reads.

## Decision

1. **Layout: one prefix per inspection, five kinds.**
   `<org>/<site>/<inspection>/{video,frames,sparse,dense,bundle}/…`, with the catalog ids as path
   parts and `_local/_local/<run name>` for runs outside the catalog (CLI, benchmark, refresh).
   The kinds are the step outputs the product keeps, under the names that describe them, not the
   step or tool names (`bundle`, not `export`; `sparse`, not `colmap`). `layout.KIND_DIRS` is the
   single mapping from a kind to its run sub-dir and is shared by the pipeline (push/pull) and
   the API (path → key). `probe/`, `run.json`, `log.txt`, `workflow.json` are not stored.
   Alternative considered: a per-run prefix (`…/<inspection>/<run>/…`). Rejected for now: one
   inspection has one capture in the current model (`POST /api/inspections/{id}/runs` queues the
   newest run), and a per-run level would duplicate the immutable video for every rerun. When an
   inspection gets several captures, a `<capture>` level goes under `<inspection>`; the kinds
   stay.
2. **Immutable: `video`, and `frames` by convention. Rebuilt: `sparse`, `dense`, `bundle`.**
   `S3Storage.put` refuses to overwrite a key under `video/`; the pipeline pushes the video only
   into an empty prefix; a second upload with the same name for an inspection is a 409. Frames are
   the evidence of every finding and no later step touches them; only a forced rerun of the
   `frames` step replaces them. A rebuilt kind replaces its whole prefix on every rerun (stale
   objects deleted), so storage mirrors the step's last run exactly.
3. **`data/runs/<run>` is a cache.** `run_step` pulls, before a step, the kinds it reads and its
   own kind when the local dir has no `done` marker, and pushes the step's kinds after the marker
   is written (the marker is part of the kind). So a wiped cache is **refilled, not recomputed**:
   a re-queue after `rm -rf data/runs/<run>` downloads the finished steps and reruns only `probe`.
   A push failure marks the step `failed`. The worker passes `--prefix <org>/<site>/<inspection>`
   on the command line of every step, so the prefix never depends on a file in the cache.
   `QARAU_S3_BUCKET` unset = local-only mode, byte-for-byte the pre-D1 behaviour (benchmark,
   tests).
4. **Serve: cache hit from disk; miss streamed by default; 302 to a signed URL as an option.**
   `QARAU_FILES_MODE=stream` keeps the API the only origin the browser talks to, so the existing
   viewer works unchanged and no CORS rule is needed on the bucket. `redirect` (short-lived signed
   URLs, `QARAU_SIGNED_URL_TTL_S`, default 300 s) takes the bytes off the API when a deployment
   can put CORS on its bucket; it is one setting, same routes, same shapes. Both resolve the key
   through the catalog (or the `_local` prefix) and `KIND_DIRS`, never through a filesystem path.
5. **Libraries.** boto3/botocore (Apache-2.0) for the client; path-style addressing and SigV4 so
   one configuration works for Versity, AWS and any other S3-compatible server; moto (Apache-2.0)
   as the in-memory S3 in tests, never the dev Versity (no endpoint is set in tests, so moto
   intercepts every request). The backend imports the pipeline's `storage.py` (path dependency,
   ADR 0023) instead of copying it.

## Consequences

- `infra/.env.example` documents `QARAU_S3_ENDPOINT/BUCKET/ACCESS_KEY/SECRET_KEY/REGION`,
  `QARAU_FILES_MODE`, `QARAU_SIGNED_URL_TTL_S`; the compose worker gets them and depends on the
  `s3` service. The API's old `S3_*` settings keep working as fallbacks for endpoint and keys.
- A `--force` rerun of `frames` overwrites the frames in storage; findings made on the old frames
  (DEF1) keep pointing at frame indices that may now mean other images. Until findings pin a
  frames version, a forced `frames` rerun on an inspection with findings is an engineer's
  decision, not an automatic one.
- `GET /api/runs` lists only the cache. A run that exists only in storage is reachable by id
  (the inspections API knows it) but not in that legacy list; it is for the pre-B1 viewer flow.
- Objects are listed on every step start (one `ListObjectsV2` per kind): negligible against a
  COLMAP run; a manifest of each kind can replace it if a step ever has thousands of objects.
- Not here: lifecycle rules (expiring `sparse/` after the bundle exists), multi-region, one
  bucket per customer and bucket policies (M5), encryption at rest beyond the server's default.
