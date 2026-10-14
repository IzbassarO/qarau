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
- [`docs/tasks/M1.md`](docs/tasks/M1.md) — the first build task
- [`docs/design/`](docs/design/) — design tokens, screen prototypes, 3D viewer spec

## Local development

Requirements (added as milestones land): Python 3.12, Node.js 22, Docker, COLMAP, FFmpeg.

```bash
cp .env.example .env
docker compose -f infra/docker-compose.yml up -d   # Postgres + MinIO (from M0)
```
