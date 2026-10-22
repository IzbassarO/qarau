# Qarau Vision

Video of industrial assets in, a 3D point-cloud map of the site out — with AI-detected
defects placed on it, changes tracked between inspections, and an inspection report an
engineer approves.

> Private. Proprietary. Not licensed for use outside Qarau.

## Modes

- **Quick Scan** — one video → map, defects, ranked list of places to check.
- **Monitoring** — a baseline map of the site; every new video is aligned to it and the
  result is *change*: new defects, defects that grew, areas that stayed stable.

Every finding links to the frames it came from, and the map shows what was **not seen**,
so the absence of red never pretends to mean "all clear".

## Status

Milestone **M0 → M1**: repository and dev environment; video → point cloud → web viewer.
See [`docs/roadmap.md`](docs/roadmap.md).

## Docs

- [`CLAUDE.md`](CLAUDE.md) — product, architecture, stack and working rules (start here)
- [`docs/architecture.md`](docs/architecture.md) — layers, pipeline, data flow
- [`docs/roadmap.md`](docs/roadmap.md) — milestones to pilot-ready
- [`docs/decisions/`](docs/decisions/) — architecture decision records
- [`docs/design/`](docs/design/) — design rules, finding prototype, 3D viewer spec

## Local development

Requirements: Docker, [uv](https://docs.astral.sh/uv/) (Python 3.12), Node.js 22 + pnpm
(for `web/`), FFmpeg and COLMAP (for `pipeline/`).

```bash
cp .env.example .env          # safe dev defaults; never commit .env
make up                       # Postgres 16 + PostGIS + pgvector, Versity S3 Gateway (waits until healthy)
make api                      # FastAPI on :8000 (use `make api API_PORT=8001` if 8000 is taken)
curl -s localhost:8000/health # {"status":"ok","db":"ok","storage":"ok"}
make test lint                # backend always; pipeline/ and web/ when present
make down
```

Ports: Postgres `5433` (host), S3 `7070`, API `8000`, web `5173`. Object storage is any
S3-compatible server ([ADR 0006](docs/decisions/0006-object-storage-after-minio.md)); the API only
needs `S3_ENDPOINT` and keys from `.env`. The pipeline output in `data/runs/` is served by the
API under `/api/runs`.
