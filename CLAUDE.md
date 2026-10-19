# Qarau — guide for coding agents

This file is the source of truth for anyone (human or agent) working in this repository.
Read it fully before the first change. Codex reads `AGENTS.md`, which points here.

## What we are building

**Qarau Vision** turns video of industrial assets (drone, phone, walkdown camera) into a
**3D point-cloud map** of the site with **AI-detected defects placed on it**, tracks how
those defects **change between inspections**, and produces an **inspection report** an
engineer approves.

- Company: Qarau. Product: Qarau Vision.
- First market: oil & gas in Kazakhstan (pipelines, tanks, above-ground equipment).
  Next: power grid, mining, then construction and residential buildings.
- Business model: software, not a service company. SaaS subscription for private
  customers; on-premise licence + yearly support for state companies that cannot send
  footage to a cloud.
- Horizon: a product ready for pilot integration by spring 2028. This is a startup,
  not a hackathon project. Build things that will survive real customers.

## The two modes

Both modes share one pipeline. Quick Scan is built first; a Quick Scan of a site becomes
that site's baseline for Monitoring.

1. **Quick Scan** — one video in → point-cloud map, defects on the map, a ranked list of
   places to check. No history. Used for one-off inspections, construction, residential
   buildings, and later for individual users.
2. **Monitoring** — a detailed first survey builds a **baseline map** with a named
   structure (pipe start, tank #7, support #112). Later videos (shorter, partial) are
   **registered to the baseline**: each new frame is localized against it, then the new
   points are aligned (ICP). Output is **change**: new defect, defect grew
   (0.2 m² → 0.4 m²), stable. Every point on the map has a history.

## Trust rules (non-negotiable)

Engineers will not trust a black box. Every output must be checkable.

- **Every finding links to evidence**: the source frames (several angles when available)
  with the segmentation mask drawn, model confidence, measured size, and a plain-language
  reason for its risk class.
- **Coverage is shown, not assumed.** Each region of the map is in exactly one state:
  - `inspected_ok` — seen with adequate quality, nothing found (blue)
  - `finding` — defect found; severity `critical` (red) or `watch` (orange)
  - `not_seen` — not in any usable frame (gray)
  Never present `not_seen` as OK. Reports must list not-seen areas.
- **Footage quality is checked before analysis** (blur, motion, glare, distance, missing
  sides) and reported as "what to re-shoot".
- **A human decides.** The system proposes; an engineer confirms or rejects each finding.
  Every decision is stored in the decision log (who, when, what, why). The decision log
  is our future training data — never lose it, never edit it in place.
- **No silent model output.** If a stage fails or confidence is low, say so in the UI.

## Architecture

Four layers. The same containers run in the cloud, on our servers and on a customer's
server; only storage addresses and the LLM backend change via config.

1. **Capture** — drone video (+ GPS/telemetry from SRT/metadata), phone app, desktop app
   import from SD card.
2. **Storage** — S3-compatible object storage (Versity S3 Gateway in dev, ADR 0006; S3 in cloud) for video,
   frames, point clouds (LAZ for exchange, Potree octree for viewing); PostgreSQL 16 with
   PostGIS (geo) and pgvector (similar-defect search).
3. **Processing** — GPU workers, jobs from a durable queue. Stages are idempotent and
   write their outputs to storage, so any stage can be re-run alone.
4. **Delivery** — FastAPI, web app (React + Three.js), desktop app (Tauri, same React
   code), PDF/XLSX reports.

### Pipeline stages

```
ingest → frames (+ quality check) → reconstruction → segmentation → projection
       → measurement → [monitoring: registration → change detection]
       → coverage → risk ranking (LLM) → report
```

| Stage | What | Default tool |
| --- | --- | --- |
| ingest | store video, read telemetry/GPS, create Capture | FFmpeg, exiftool |
| frames | extract keyframes, score blur/exposure/motion | OpenCV |
| reconstruction | camera poses + point cloud | COLMAP (product path); VGGT for research only |
| segmentation | defect masks per frame from text prompts | SAM 3.1 |
| projection | masks → labelled 3D points via camera poses + depth | NumPy, Open3D |
| measurement | defect area (m²), extent; scale from GPS or reference object | Open3D |
| registration | localize new frames in the baseline, align clouds (ICP) | COLMAP image registration, Open3D ICP |
| change detection | compare findings/points between inspections | Open3D, PostGIS |
| coverage | which map regions were seen with usable quality | own code |
| risk ranking | combine findings with asset data, rank, explain | LLM (Muse Spark via Meta Model API, or open Meta model via vLLM on-prem) |
| report | inspection report + work orders, RU / KK / EN | LLM + templates |

### Model licences (check before any commercial use)

- **VGGT / VGGT-Ω weights are CC-BY-NC (non-commercial).** Use only for research and
  comparison. The product path uses COLMAP (BSD) unless Meta grants a commercial licence.
- **SAM 3.1** is under the SAM License — verify commercial terms before selling.
- Keep every model behind an interface (`Reconstructor`, `Segmenter`, `Reasoner`) so it
  can be swapped without touching the rest.

## Repository layout (target)

```
backend/     FastAPI app: api/, models/ (SQLAlchemy), schemas/, services/, alembic/
pipeline/    qarau_pipeline package: one module per stage, interfaces, CLI entry points
web/         React 19 + TypeScript + Vite + Three.js + MapLibre + Tailwind
desktop/     Tauri 2 shell around web/ (added at milestone M4)
mobile/      capture app (later)
infra/       docker-compose, Dockerfiles, Ansible for on-prem install
docs/        architecture, decisions (ADRs), roadmap, design
scratch/     your experiments — gitignored, never imported by product code
data/        local test videos and outputs — gitignored, never committed
```

Create folders when the first real file needs them, not before.

## Stack

| Layer | Choice |
| --- | --- |
| Language | Python 3.12 (backend, pipeline), TypeScript (web, desktop) |
| API | FastAPI, Pydantic v2, SQLAlchemy 2, Alembic |
| Queue | Valkey + RQ (Temporal later if needed); not Redis (licence, ADR 0012) |
| DB | PostgreSQL 16 + PostGIS + pgvector |
| Storage | S3-compatible: Versity S3 Gateway (dev, ADR 0006), S3 in cloud |
| 3D | COLMAP, Open3D, laspy, PotreeConverter |
| Vision | SAM 3.1, OpenCV, FFmpeg |
| LLM | provider interface: Meta Model API, local vLLM, Anthropic API for dev |
| Web | React 19, Vite, Three.js, MapLibre GL, TanStack Query, Tailwind |
| Desktop | Tauri 2 |
| Infra | Docker Compose now; Kubernetes/Nomad later; Ansible for customer installs |
| CI | GitHub Actions: lint, tests, builds |
| Quality | ruff + mypy (Python), ESLint + Prettier + tsc (web), pytest, Vitest |

## Core data model

`Organization → Site → Asset`; `Inspection (mode: quick_scan | monitoring) → Capture
(video) → Frame`; `Reconstruction (point cloud, camera poses)`; `BaselineMap (per Site)`;
`Finding (defect: class, severity, area, 3D location, status)`; `Observation (a Finding
seen in one Frame: mask, confidence)`; `CoverageRegion (state)`; `Change (Finding vs
previous Finding)`; `Decision (append-only: user, verdict, comment, time)`; `Report`.

Every customer's data is isolated (separate schema or database). Customer data for
Kazakhstan stays on servers in Kazakhstan.

## Viewer look (product, not decoration)

- Black background, points coloured by height in shades of blue/white so terrain,
  buildings and pipes read without texture.
- Findings: red = critical, orange = watch. Not seen: gray. Inspected OK: the base blue.
- Click a finding → evidence panel (frames with mask, confidence, size, reason, history,
  confirm/reject). Layer toggles: terrain, structures, pipes, findings, coverage.
- UI language: Russian, Kazakh, English (i18n from day one; no hard-coded strings).

Full screen specs and interactive prototypes: `docs/design/` (start with
`docs/design/viewer-3d.md` — the 3D viewer with video ↔ map sync is the core screen).
Visual rules for every page (tokens, light/dark, density): `docs/design/DESIGN.md`, then the
frontend-design skill in `.claude/skills/` (pinned, Apache-2.0). Agents never download skills.

## How we work: you own the engineering

This is a startup building a SaaS product, not a pet project. The owner (Izbassar) sets
product direction and **observes; he does not review or approve code**. **You are the
engineering team** and are expected to act like a senior engineer who owns the outcome.
Automated gates replace human review (see "Delivery" below).

You decide on your own (and record non-obvious choices as an ADR in `docs/decisions/`):
- libraries, code structure, data formats, algorithms, test strategy, refactors;
- how to split a task into steps, branches and commits;
- when to research (read docs, papers, source of COLMAP / Three.js / Potree) before coding;
- using subagents in parallel for independent work (e.g. pipeline and viewer at once,
  one agent researching while another builds, a reviewer agent on your diff).

You must:
- **Plan before large tasks**: write the plan to `docs/notes/<task>-plan.md`, then build.
- **Verify, don't assume**: run the code, run tests, open the result, look at the
  screenshot. "It compiles" is not done.
- **Keep `docs/notes/progress.md` current**: what is done, what is next, known issues,
  decisions waiting for the owner. Short, newest first.
- **Commit in small, meaningful steps** on a feature branch; open a PR into `dev` with `gh`,
  with screenshots and what you verified, then report to the owner. A Claude Code session
  never merges it (see "Delivery").
- Leave the code better: if something in this file is wrong or missing, propose the change.

Stop and ask the owner only for: spending money or adding a paid service; anything about
model or data licences; deleting data or history; changing product scope or the two
modes; security-sensitive defaults. Everything else — decide, document, continue.

## Delivery: automated gates, no human review

- Branches: work happens on `devteam/<task>` (dev team) or a feature branch, always via a
  PR into **`dev`**. The PR description is the review packet: what changed in plain words,
  how to try it, acceptance criteria, checks, gates.
- A PR into `dev` is merged (merge commit, exactly the tested commit) only when **all** hold:
  local checks green, the tester judge passes, GitHub CI green, no benchmark regression
  (when `pipeline/` changed; `devteam/config.yaml` → `gates.benchmark`), and the secret scan
  (gitleaks) is clean. Otherwise the work goes back to the developer, at most 3 times,
  then the task is **blocked** and listed in the daily report.
- **Who merges** (owner, 2026-10-19): Kerege's gated auto-merge of its own task PRs
  (`devteam/<task>` → `dev`) stays as described above. A Claude Code session (interactive or
  any other session outside Kerege) **never merges a PR into `dev` or `main`**, even with every
  gate green: it opens the PR, reports what it verified, and the owner merges.
- `dev` → `main`: an automatic weekly release PR, merged only if CI and the full benchmark
  are green on `dev`. Nothing else reaches `main`.
- **Forbidden for every agent:** force-push; rewriting published history (rebase, amend,
  filter); deleting branches other than its own; disabling or editing CI to pass, or
  touching branch protection; `gh pr merge --admin`; committing secrets or anything under
  `data/` (or `.env`, `scratch/`).
- No GitHub Pro (owner, 2026-10-15), so no server-side branch protection:
  `devteam/hooks/pre-push` refuses force-pushes, deletions, direct pushes to `main`/`dev` and
  moving an existing tag. One exception (owner, 2026-10-16): devteam's task-status commits
  (`backlog: P3 done (#18)`) go straight to `dev` as fast-forwards that change only
  `devteam/backlog.yaml`; never to `main`. Every successful `dev` → `main` release is tagged
  `release-YYYY-MM-DD` (`.2`, `.3` for a second one that day): the rollback points.
- The owner reads `devteam/reports/YYYY-MM-DD.md` (tasks done, merged PRs, cost, benchmark,
  blocked tasks). `devteam stop` halts everything at any moment.

## Working rules for agents

- **Never leave a server running** (owner, 2026-10-17). Any dev server, preview, dashboard or
  test server an agent starts is stopped as soon as its check is done; never leave a listening
  port behind. If the owner wants a server, he starts it himself. Kerege kills everything a
  developer or tester step started when the step ends and logs a warning for any port it had
  to close; Playwright always starts its own fresh server (`reuseExistingServer: false`).
- **Cost first.** Do not add paid services, cloud resources or API usage without the
  owner's explicit OK. Prefer local, open-source, free tiers.
- **Small changes.** One feature or fix per branch/PR, with tests; the gates must be able
  to judge it.
- **Never commit** videos, point clouds, model weights, `.env`, keys, or customer data.
- **Update docs in the same change** when behaviour, API or architecture changes
  (`docs/architecture.md`, ADR in `docs/decisions/` for real decisions).
- **Schema changes only via Alembic migrations.**
- **Every API route declares a role**; no unauthenticated routes except health checks.
- **Test on real footage.** A stage is not done until it runs on a real phone video in
  `data/samples/` and its output is inspected visually.
- **Licences:** before using any model or library, check that its licence allows
  commercial use and note it in the task/PR; unclear → ask the owner.
- **Ask when a requirement is ambiguous** instead of guessing; note assumptions in the PR.
- Commit messages in English, imperative ("Add frame quality scoring").
- Talk to the owner in Russian unless asked otherwise.

## Milestones

| | Goal | Done when |
| --- | --- | --- |
| M0 | Repo, docs, dev environment | `docker compose up` starts Postgres + S3 storage + API health check |
| M1 | Video → point cloud → web viewer | A phone video becomes a black-background point cloud viewable and rotatable in the browser |
| M2 | Defects on the map | SAM 3.1 masks projected onto 3D points; click a red point → frames + mask + confidence |
| M3 | Quick Scan complete | Coverage states, quality check, measurement, ranked list, PDF report, decision log |
| M4 | Monitoring | Baseline map, registration of a new video, change detection, per-point history; Tauri desktop build |
| M5 | Pilot-ready | On-prem install in one command, roles/SSO, audit log, real customer footage |

Current milestone: **M0 → M1**.
