# Progress

Newest first. Keep it short.

## 2026-10-22 — Backlog: demo chain first, metres and auto-detection LATER

- **Owner decisions:** product first (website + deploy), then metres; until a ScaleReference exists
  every area is "scale not set". Automatic defect detection (SAM, training, DEF2) is stopped until
  Kubernetes. Manual marking, confirm/reject and the append-only decision log stay. Design v2 comes
  from Claude Design (task DS1, blocked until the owner's files land). Every task's goal ends with
  "Done when" checks.
- **Backlog:** new chain B1 → U2 → S1 → DS1 → D1 → DEF1-api → UI-inspections → REV1-api → UI-evidence →
  RPT1 → UI-mark → UI-report → D2 → COV1 (`devteam/backlog.yaml`, `notes` key holds the rules: YAML
  comments die at the first status commit). New status `later` for the LATER section (P5a-1, P5a-2,
  P7, P4a-2b, P4c, A7a, A7, O1a, O1, P5b, P6, UX-kk, UX-390, A8, ACC1): the brain never plans it,
  dependencies never un-block it, `devteam resume` leaves it. P4a-2a-run → done (PR #58).
- **Next:** owner merges the backlog PR, then `devteam rehearse` (PASS expected: B1), then one attended
  real run of B1.

## 2026-10-22 — Kerege: lone-surrogate crash, crash isolation

- **Crash 2026-10-21 20:29:** UX-dense's tester crashed with `'utf-8' codec can't encode
  character '\udcdc'`. Cause: the branch adds a binary fixture
  (`web/src/fixtures/dense-bundle/dense.color.u8`, byte 0xDC); `Team.git()` decoded the diff
  with `surrogateescape`; the judge's `llm_call` `.encode()`d the prompt. Fixed: subprocess and
  git output decoded with `replace`; node outputs, events, the LLM prompt and file writes cleaned.
- **Crash isolation:** `loud()` logged "the others go on" and re-raised, which ended the whole
  run. Now a crash blocks only its task (`CRASHED in <step> at <file:line>: …`), the run ends
  normally; `devteam resume <task>` continues it. Usage limits and budget stops still stop the run.
- **Waiting for the owner:** merge the PR, then `devteam resume` (UX-dense's test step re-runs).

## 2026-10-21 — P4a-2a-run: first real COLMAP dense on Modal (notre-dame-p3e)

- **Done on notre-dame-p3e:** L4, 121 views at 640x480. PatchMatch photometric 11.6 min +
  geometric 15.0 min (~5.8 s per view per pass), fusion 12 s; whole job 1681 s of the 1800 s
  timeout. 837 928 fused points; bundle `data/runs/notre-dame-p3e-modal` keeps 730 069 (107 859
  far ones dropped, ADR 0017), all linked to frames (`colmap_fusion`), reproj check 0.47 px.
  A few hundred sky points float above the church. Cost: L4 $0.368 + sandbox CPU $0.070 +
  memory $0.005 ≈ **$0.44**. Image build (sm_89 only, 7 min, three tries) ≈ $0.23.
- **First try failed on capacity:** Modal queued the L4 sandbox 28 min, then
  `ResourceExhaustedError` (≈ $0, queue time is not billed). Now: `start_timeout_s` 300 s per
  GPU type, queued sandbox terminated by job tag, then `gpu_fallback: [L40S]` (owner); the
  ledger books GPU hours from the worker's start, never the queue.
- **Fixed on the way:** modal 1.6.2 has no `Sandbox.open/mkdir` (now `sandbox.filesystem`);
  the Dockerfile had never been built (`libopencv-core-dev` for OpenImageIO's CMake config on
  Ubuntu 24.04; base image user `ubuntu` held UID 1000); CUDA arch is a config build arg
  (`dense_remote.cuda_architectures: "89"`, owner).
- **Not run: orono-p3e** (151 frames at 1920x1080 ≈ 2–3 h at this pace, over the timeout).
  Next: P4a-3 (PatchMatch speed knobs, target < 30 min for a 3-min 1080p video), then the owner
  runs orono. The viewer does not draw the dense layer yet: UX-dense.

## 2026-10-19 — Backlog: Modal, reference sheet size, thin clients, UX brief

- Modal GPU smoke test passed (T4, CUDA in PyTorch, ~$0.003, nothing left running). The $30
  workspace usage limit is not visible from the CLI: owner checks it in the dashboard.
- dev_pipeline order: P3e → P4a-1b → P5a-1 → P5a-2 → D1 → S1 → O1a → A7a. P4a-1b is mocks
  only (no real Modal call); P4a-2 (first paid GPU run) stays blocked until P4a-1b is merged
  and the owner gives the command. P3e and P5a-1 are blocked: `devteam resume` them.
- P5a-2: sheet size letter (default) / a4 / custom, shown in the run report.
- CLAUDE.md: thin clients rule (web, desktop, mobile use only the backend API; OpenAPI).
- UX1a / DESIGN.md: product style brief, tokens in web/src/design/tokens.json, screenshots at
  1440x900 (mobile width waits for multi-viewport screenshots in Kerege).

## 2026-10-19 — Kerege run 4 fixes (after the P3e rehearsal)

- Tester screenshots sign in first (A1b login, test user from devteam/.env); "/?signed-out"
  shows the login page. P3e was blocked only because "/" showed the login form.
- Plan check: `/viewer/<run>` routes must show the task's own output (P3e's routes named old
  runs). The data snapshot of a blocked task is kept, so its own runs stay "its own".
- Loop detection by criterion number, not wording.
- UX1a (design system, login page, control centre) and UX1b (run list, viewer chrome), first
  in dev_app order. frontend-design skill (anthropics/skills dbd4588, Apache-2.0) in
  .claude/skills, `.agents/skills` symlink for Codex; rules in docs/design/DESIGN.md.
- Owner: add QARAU_TEST_USER / QARAU_TEST_PASSWORD to devteam/.env (any non-empty pair works
  on the dev server).

## 2026-10-19 — Kerege run 3 (18–19 Oct) fixes

- `claude` vanished at 23:39 (failed self-update) and stopped the run: developer CLIs are
  resolved to absolute paths at run start and checked before each round (one retry after 30 s);
  any crash now pauses only that developer, its round resumes next run.
- P3e "protected: backlog.yaml" was a false positive: the orchestrator merged dev into
  devteam/P3e, the step crashed (the missing `claude`), and the resumed round counted dev's
  changes as the developer's. Fixed (a dev merge left by a crash is recognised). The main
  checkout lagged one status commit behind (backlog.yaml modified): now synced to the pushed
  commit; `in_progress` is pushed too.
- P3e "manifest.json not found": its runs are in data/runs, the tester's viewer read the quick
  benchmark output. One setting, `runs_dir` (QARAU_RUNS_DIR), for developer, tester and e2e.
- P5a split (8.2 M tokens in one round, limit 8 M kept): P5a-1 distance + gravity (continues
  on devteam/P5a-1 from P5a's round), P5a-2 A4 sheet (depends on P5a-1). P5a dropped.
- Owner: `devteam resume P3e` and `devteam resume P5a-1` after the merge.

## 2026-10-18 — Kerege run 2 (cont-20261018-153430) fixes

- dev CI was red from 778e072 (`backlog: A9-fix done (#41)`): a devteam test asserted live task
  statuses in the real backlog; it now checks structure and order only, and every status
  commit runs the backlog tests on what it would push first (not pushed if they fail).
- dev's e2e had 3 failures nobody saw (e2e never ran): A6's test still expected Observer
  disabled after A9 (#37) made it real; fixed in `web/e2e/a6-navigation.spec.ts`.
- Tester runs Playwright e2e (fresh Vite on a free port, clean checkout) for web/ changes; a
  Codex sandbox cannot open a port. A5-fix can be resumed (`devteam resume A5-fix`).
- P3e was killed at 60 min while its refresh had finished (5 runs `*-2026-10-18` in data/runs,
  code on devteam/P3e). Supervisor: idle = no output and no CPU work (20 min); per-task
  `supervisor:` overrides (P3e: 180 min); killed Claude rounds now record output tokens.
- O1 split: O1a (backend, after S1) + O1 (web, depends on O1a).
- 2 blocked in a row now pauses that developer only; the dashboard ignores dropped connections.

## 2026-10-18 — Kerege follow-ups

- dev_app owns `web/` plus markdown in `docs/decisions/` and `docs/notes/` (plans, ADRs).
- Scope-gate replay on merged PRs found three historic violations:
  - A9 (#37) loosened a test threshold (0.5 → 0): task **A9-fix** restores it in code.
  - A5 (#32) `test.skip(!manifest.far)` never runs (no far layer locally, no data in CI): task
    **A5-fix** makes that e2e run on a synthetic bundle.
  - P3c (#23) edited `pipeline/qarau_pipeline/benchmark_set.toml` (now a protected path):
    **approved exception** by the owner (2026-10-18); the change stays.
- GPU: task P4a-1b (queued right after P5a) adds a Modal provider (mocked in tests, token
  from devteam/.env, never printed); budget 1.0 GPU h/day, 0.5 h per job. P4a-2 stays blocked
  until the owner says the Modal account is ready.

## 2026-10-17 — Kerege supervisor (A7 used 42.6 M tokens while going nowhere)

- Per-task limits in `devteam/config.yaml` → `supervisor` (tokens per round / per task, minutes
  per round), counted live; over a limit only that task stops and is blocked with the reason.
- Same tester reason twice in a row → blocked; dev merged in before every round, conflicts get
  a short resolve step; plans that need another developer's folders are blocked for a split.
- `devteam stop <task>` / `devteam resume <task>`; dashboard agents only from the current run;
  prompt caching for brain + tester with cache hits in the dashboard and report.
- Backlog: A7 split into A7a (backend ROI API, dev_pipeline) + A7 (web, resumes on its branch);
  new P3e (refresh viewer runs), U2 (upload in the web app), D2 (deploy online); S1 no longer
  waits for dense. dev_pipeline order: P3e, P5a, D1, S1, A7a. A8 waits for U2.

## 2026-10-17 — Owner rule: no agent leaves a server running

- CLAUDE.md / AGENTS.md: any server an agent starts is stopped when its check is done.
- Kerege kills everything a developer / tester step started (process group + `KEREGE_STEP`
  marker) and warns with the port number of any server it had to close; step output goes to
  files, so a background server no longer holds a step open until its timeout.
- Playwright and Kerege screenshots always use a fresh server; a port that already answers
  fails loudly instead of testing a stale server.

## 2026-10-17 — Kerege: continuous mode, dashboard; home shows real runs again

- Regression: since A3 (#20) the control center home always rendered the demo site and never
  requested `/api/runs`, so real runs vanished from the sidebar. Fixed (home lists real runs,
  demo only at `/inspections/demo`), with unit + e2e tests. `/viewer/<name>` was never broken.
- Frustums / build-up "on" in the demo: not stored preferences (only the colour mode is stored,
  under a v2 key). Cause: stale dev servers and an open tab that kept React state through hot
  reload. A plain reload shows the A4b defaults (both off).
- devteam is now called **Kerege** (README, CLI output). New: `devteam run --continuous`
  (ADR 0014) and `devteam dashboard` (local read-only status page).
- Backlog: dev_app A6, A6b, A7, A9, O1, A8 (after P5a), then A5; dev_pipeline P2b first
  (existing branch, PR #25), new P4c (depends on P4a, A7).

## 2026-10-16 — P3b waits for the viewer fix (A4); task dependencies in devteam

- P3b's pipeline work is done on `devteam/P3b`; it failed only the default view (dark
  "not yet seen" colouring, big camera frustums): a viewer problem. A4 (#22) was built to its
  old criteria: robust framing and a colour toggle, but the default is still height-blue with
  the dark build-up colouring and frustums shown. New **A4b**: real RGB and frustums hidden by
  default. P3b is blocked on A4b and is re-tested after it against new criteria (no band of
  points in the sky, compared with dev).
- devteam: `depends_on` (auto back to `todo` when done), resume on an existing task branch,
  `dev-*.png` comparison screenshots for pipeline tasks, scratch dir outside the worktree.
- Queue: dev_app A4b; dev_pipeline P2b. P3c is done (#23). P4a waits for a GPU (blocked).

## 2026-10-16 — Dev team re-planned P0 and A2a (incident)

- Cause: devteam writes task status only to the working copy of `devteam/backlog.yaml`; it
  was never committed, so on `dev` P0, P3, A2a and A2b were still `todo`. Before today's run
  the working copy was reset to `dev`, the statuses were lost and the brain planned P0 and
  A2a again. The reruns committed only to local branches; the gate saw PR #14 / #15 already
  merged and marked them done: nothing reached `dev`, no new PR.
- Fix: backlog statuses for every merged devteam PR are now on `dev`. The brain skips tasks
  whose `devteam/<id>` branch is merged into `dev`; `devteam run` refuses to start while
  the backlog's status fields differ from `origin/dev`.
- devteam now owns the statuses: when a task ends it commits them straight to `dev`
  (`backlog: P3 done (#18)`, backlog-only fast-forward, allowed by the pre-push hook) and
  fast-forwards the main checkout. The gate no longer takes an old merged PR as this run's:
  done needs this run's commits merged through a PR with green CI.

## 2026-10-16 — Backlog after P3 merged; screenshots for pipeline tasks

- P3 is done (#18). The sky work moves to a new task **P3b** (sky mask, far-point filter,
  `far` layer; ADR 0013 when built); **A5** now waits for P3b. New **P3c**: mykolaiv still
  gives 2 models / 84.8 %, goal 1 model ≥ 90 % and gated again, or a written reason.
- Dev team: a task whose acceptance mentions a visual result in the viewer needs the
  tester's own screenshots, pipeline tasks too (the viewer serves the developer's quick
  benchmark output). No screenshot → the task fails, whatever the judge says. A plan whose
  criteria mention the viewer but list no routes is rejected. A task may pick its own
  quick benchmark videos (`quick_only`), e.g. P3b uses notre-dame and aegviidu.

## 2026-10-15 — Owner's review of the first maps

- **Sky wall on long shots.** Long outdoor videos grow a wall of far, badly triangulated
  points in the sky that hides buildings and trees in the default view. Planned for P3,
  moved to P3b on 2026-10-16: sky masks before feature extraction, a filter for
  low-parallax (< 1.5°) and high-reprojection points with counts in the run report, and surviving far points exported as a separate
  `far` layer (gray), never mixed into the object cloud.
- **Product UI** (screens in docs/design/) is a later task U1, after geometry P3/P4; the
  current viewer is a dev tool.
- New task **S1**, a server job queue with per-stage progress, placed after P4 and blocked
  by P4a. Queue server: **Valkey + RQ, not Redis** (owner; Redis ≥ 7.4 is RSAL/SSPL, ≥ 8
  AGPL; ADR 0012). Its web part becomes a separate `dev_app` task when S1 unblocks.
- New task **A5**, the far-point layer in the viewer (gray, toggle in Show), blocked by P3b.

## 2026-10-15 — Dev team: retries and tester-owned screenshots

- A2a's tester crashed on one transient "Connection error". `llm_call` now retries
  connection errors, timeouts, 429, 5xx and mid-stream overload after 15 s, 60 s and 180 s
  (each try reserves its own budget, each retry is logged), then fails loudly as before.
- A2a was sent back because Codex did not produce a screenshot; it cannot (sandbox, no
  network, no browser). The brain no longer asks developers for screenshots, the developer
  brief says not to try, and the tester judges visual criteria from its own screenshots of
  the task's `routes`, also for tasks planned before this change.

## 2026-10-15 — Owner decisions

- **No GitHub Pro.** The local `devteam/hooks/pre-push` stays (now also refuses moving a tag).
  Every successful `dev` → `main` release is tagged `release-YYYY-MM-DD` (`.N` for a second
  one that day) and pushed; `devteam release` does it. The first release (PR #10) is
  tagged `release-2026-10-15`.
- **PostGIS:** fine as a separate, unmodified database server reached over SQL (ADR 0010).
  On-prem installs that ship the image pass on its licence and source offer.
- **SAM 3.1 licence:** decided later, before the defect phase. Until then no task may depend
  on SAM 3.1 weights for anything sold.
- **Depth Anything V2:** only Small (Apache-2.0). `wip/dense-stage` already defaults to
  `Depth-Anything-V2-Small-hf` and ADR 0008 forbids Base/Large, but the model id is a
  parameter, so P4a must refuse any other id in code (backlog P4a updated).

## 2026-10-15 — Dev team v2: Codex in parallel; first real parallel run

**Done**
- v2 (PRs #6, #7 and this one): `dev_app` = Codex (`codex exec --json`, `exec resume` for fixes,
  ChatGPT login, no API key) runs in parallel with `dev_pipeline` (Claude Code). Developers
  run only the quick benchmark (notre-dame + room, ~11 min) and get the cached dev numbers;
  the full set runs at the merge gate and release, cached per `pipeline/` tree. Tester sees
  screenshots of web routes. Daily report: subscription load per task (rounds, minutes,
  tokens, ≈ API $). P3 moved right after P2a.
- First real parallel run (`--only P2a,A1a`): A1a merged (PR #8; Codex, 1 round, 20 min,
  7.4 M tokens in, mostly cached), P2a merged (PR #9; Claude Code, 2 rounds, 56 min, ≈ $10
  notional), first release `dev` → `main` (PR #10). API: $0.56.
- Fixed during the run: parallel `git worktree add -b` raced on `.git/config` (A1a died
  silently) → orchestrator git calls take turns; a crashing parallel task now says so at once.

**Gate hole found and fixed (this PR)**
- The dev baseline benchmark failed (mykolaiv export: laspy OverflowError after a good
  reconstruction) and the gate called that "not available yet (skipped)": **P2a merged and
  was released without a benchmark comparison.** Checked afterwards: P2a extracts
  byte-identical frames for mykolaiv, so it changed nothing there; mykolaiv's drop
  (178 → 81/210 registered) is COLMAP noise on identical input.
- Now: partial results are kept, videos that ran in both runs are compared, a dev baseline
  with no results blocks the task, a failing video on dev blocks the release. Mykolaiv is
  shown but not gated until P3 (noise 39–85 % registered). New task P0: export must not crash
  on far outliers. The brain may no longer require edits to `progress.md` (P2a's tester sent
  the work back for it although the brief forbids it).

**Next**
- `devteam run`: P0 (export crash) and A2a (controls) in parallel, then P3.

## 2026-10-15 — P2a: robust phone import (frames stage)

- `pipeline/qarau_pipeline/probe.py`: `probe_video()` → `VideoInfo` (codec, coded/display
  size, `rotation_deg` from displaymatrix or `tags.rotate`, HDR = PQ/HLG or BT.2020 ≥ 10 bit,
  fps, `is_vfr` = a pts interval > 25 % off the median in the first 120 packets).
- `frames.py` decodes from `VideoInfo`: ffmpeg autorotate once (checked against the display
  size), HDR → 8-bit BT.709, frames picked by pts on the old `fps` grid (room/mykolaiv/orono/
  aegviidu pick the same frames as before), readable errors (no ffmpeg, unprobeable file,
  decode failure, 0 frames).
- New `frames/manifest.json`: `source` VideoInfo, `conversions` (`autorotate:90`,
  `tonemap:hable|colorspace`, `vfr:pts_sampling`), per frame `file` + source `pts_s`,
  `warnings`.
- HDR: `zscale`+`tonemap=hable` when the ffmpeg build has zscale (CI's apt ffmpeg), else the
  `colorspace` fallback with a manifest warning (Homebrew ffmpeg on the dev Mac: no libzimg,
  highlights may clip). The hable path runs only in CI; not yet seen on a real iPhone clip.
- Quick benchmark: notre-dame 109/109 (100 %), room 257/269 (95.5 %), same as before. A draft
  sampler that moved the grid dropped room to 90 %; see `docs/notes/benchmark-method.md`,
  "Sampling phase". IMG_8315.MOV: rotation 90 + HLG detected, upright 1080×1920 SDR frames.
- Next: an ffmpeg with libzimg for dev and on-prem images, so real HDR footage gets hable.

## 2026-10-15 — Dev team v1 done; handoff for v2

**State**
- No human review (owner's decision): work reaches `dev` only through the gates (local checks,
  Sonnet tester, GitHub CI, benchmark, gitleaks); weekly `dev` → `main` release. Rules:
  CLAUDE.md → "Delivery". Design: `docs/notes/devteam-plan.md`. Phase 1: `docs/plan/phase1.md`.
- Branches: `main` = PR #2 (M2 prep + room test). `dev` = main + P1 (PR #3, merged by the
  team) + devteam itself (PR #4). Work from `dev`; `feat/*` and `devteam/*` are merged.
- devteam v1 runs: brain = Opus 5.5 (API), dev_pipeline = Claude Code headless (`claude -p`,
  subscription token from `devteam/.env`), tester = Sonnet 5.5 (API). 40 tests;
  `cd devteam && uv run pytest`. Keys in `devteam/.env` (both set; gitignored).
- P1 took 3 developer rounds (~2 h 45 min, mostly the developer re-running the 70-min
  benchmark). API cost $0.31; Claude Code reported ~$14.7 notional (subscription limits).
- First release `dev` → `main` has not run yet: it runs at the end of the next
  `devteam run` (full benchmark on dev, ~70 min, then CI, then merge).
- Unmerged agent work from M2 prep, saved as branches (unverified WIP, not in `dev`):
  `wip/dense-stage` (Depth Anything V2 dense stage + export, ADR 0008 → input for P4a),
  `wip/recon-eval` (reconstruction config eval script, ADR 0007 → input for P3).

**Next: v2 = dev_app (Codex) in parallel with dev_pipeline**
- `devteam/devteam/real.py`: add `"codex"` to `RealAgents.TOOLS` and a `_codex()` beside
  `_claude()`: `codex exec --json --sandbox workspace-write -C <worktree> -` (prompt on
  stdin, same `_brief`); fix rounds `codex exec resume <thread_id> -` with the feedback.
  Parse the JSONL stream: thread id from the `thread.started` event, final message and token
  usage from the last events (check the real event names with a smoke run first). Log cost
  with `billed=False` (ChatGPT login; never pass OPENAI_API_KEY).
- The Codex sandbox has no network and cannot write `.git`: run `pnpm install
  --frozen-lockfile` in the worktree before the first round (the orchestrator commits).
- Tester for web: screenshots into the packet (Playwright in `web/`; save under
  `.state/packets/<task>/`), checks already mirror CI (`pnpm lint/typecheck/test/build`).
- Then run both in parallel: `uv run devteam run --only P2a,A1a` and show the result.

**Known issues**
- `devteam/backlog.yaml` is read from the main checkout's working tree: keep that checkout on
  `dev` (or a branch from it); status changes are written there and must be committed by
  whoever works in that checkout. Consider moving live status into `.state/` (v2 decision).
- Developer rounds that re-run the full benchmark are slow and costly on the subscription;
  consider telling developers to run `--only <short video>` and leaving the full run to the gate.
- The brain sometimes splits one criterion into several list items (seen in P1); harmless.

**Waiting for the owner**
- GitHub branch protection on `main` needs GitHub Pro (~$4/month) for a private repo;
  `devteam/hooks/pre-push` stands in locally until then.
- PostGIS is GPL-2.0: fine as a server, but on-prem installs that ship it must follow the GPL.
- SAM 3.1 commercial terms still unverified.

## 2026-10-14 — P1: benchmark command (`devteam/P1`)

- `python -m qarau_pipeline benchmark --data D --out O [--only …] [--timeout-s N] [--record]`:
  manifest `pipeline/qarau_pipeline/benchmark_set.toml` (4 public videos required, room
  optional), one child `run` per video with timeout, `O/results.json` + `results.md`, exit 0
  only if all ran. `--record` → `docs/benchmarks/<sha>.json` + rows in `benchmark.md`.
  Method: `docs/notes/benchmark-method.md`. Tests mock the child run (< 1 s).
- Product defaults (300 frames, exhaustive matching) on Aegviidu and Orono took > 28 min of
  matching each on the M1 Pro and timed out at 1800 s. The benchmark caps those two at
  150 frames (`max_frames` in the manifest, same code path; reasons in
  `benchmark-method.md`) so the set fits the gate's 90 min. Recorded run (a8efcc9+dirty,
  all 5 exit 0, required set 62 min on a loaded M1 Pro): aegviidu 132/150, 3 models,
  0.53 px; mykolaiv 178/210, 3 models, 0.74 px; notre-dame 109/109, 0.40 px; orono 150/150,
  0.57 px, 92 k points; room 257/269, 0.93 px.
- **Gate risk:** mykolaiv points/reproj swing +23 %/+16 % between identical runs, which is
  beyond the gate thresholds (`benchmark-method.md`, Variance).
- **Known issue (for the tech lead):** a real 300-frame drone video takes ~45 min on CPU. That
  is a product problem (sequential + loop matching for video? fewer features?). Measure the fix
  with this benchmark in its own task.
- Next: `scale_err_cm` once a scale reference exists (P5/P7); image-folder datasets in the set.

## 2026-10-14 — New direction: MVP plan; Step 1 room test (`feat/room-test`)

- Owner's brief arrived **without the plan text** (placeholder `<paste the plan here>`):
  `docs/plan/mvp.md`, roadmap update and `docs/tasks/phase1.md` wait for it.
- Room video (`IMG_8315.MOV`, iPhone HDR portrait): M1 registered 14 of 111 frames. Fixed in
  the frames/reconstruction stages (HDR→SDR, long-side cap, 6 fps, exhaustive matching ≤ 300)
  → 244 of 269, 26 k points. Details: `docs/notes/room-test.md`, `docs/notes/benchmark.md`.
- No A4 visible in the video → scale check needs a re-shoot.
- "Original colours" toggle already existed in the viewer; checked on the room.
- R and D agent worktrees from M2 prep left as they are (D has the dense stage, unmerged).

## 2026-10-14 — M2 prep resumed after the session stopped

- Merged: V (roll-exact camera view, object-focused initial view, bundle v2 reader,
  findings UI) and T (datasets, `docs/notes/datasets.md`, Raketa shipwreck drone video).
- R, D, S agents had stopped mid-way; relaunched in their own worktrees from where they were:
  - R: PostGIS test committed; recon eval done on notre-dame (global mapper + loop +
    overlap 20 registers 54/54 vs 52/54), Aegviidu baseline only (6 models, 127/177).
  - D: Depth Anything V2 Small (Apache-2.0) scaled to sparse COLMAP points → 7.1 M points on
    Orono (held-out depth error median 4.3 %); export to bundle v2 and viewer check pending.
  - S: segmenters + projection committed; real-footage run (Raketa) and ADR pending.

## 2026-10-14 — M2 prep started (`feat/m2-prep`)

**Done**
- PR #1 (M0 + M1) merged into `main` after owner review; CI green.
- Task `docs/tasks/M2.md` and plan `docs/notes/m2-plan.md` (bundle v2 + findings contract,
  `Segmenter`/`Densifier` interfaces, five parallel parts R/D/S/V/T). `ml` dependency group
  (torch, transformers) kept out of default `uv sync`.

**In progress (Sonnet agents, coordinator reviews before merge)**
- R: reconstruction eval (loop detection / overlap / global mapper) on all four runs, ADR 0007; PostGIS test + CI.
- D: densification without CUDA, commercial-licence models only, ≥ 1 M points on Orono, ADR 0008.
- S: segmenters (manual, local dev-only, SAM 3.1 API stub), mask → 3D projection, voting, findings, ADR 0009.
- V: through-camera roll, initial view on the object, bundle v2 reader, findings UI.
- T: CC-licensed defect footage + segmentation dataset, `docs/notes/datasets.md`.

## 2026-10-14 — Part B (pipeline) merged; M0 + M1 complete on public data

**Done**
- `python -m qarau_pipeline run --video …` runs frames → COLMAP → export end to end.
  Re-run by the coordinator on the church video: 21 s, 52/54 frames registered,
  15 215 points, bundle reprojection check mean 0.39 px.
- Three public drone videos and South Building processed; numbers in `docs/notes/m1-results.md`.
- `make lint` and `make test` green for backend, pipeline and web. e2e: 7 Playwright tests
  pass through the real API.
- Deviation: the exposure rule drops on clipped white only above 70 % (was 40 %); bright sky
  made the old rule drop a whole stretch of the Aegviidu orbit.

**Next**
- PR #1 opened (M0 + M1); CI green on GitHub. Waiting for owner review.
- M1 on our own phone footage when the owner has it (pipe, railing, building corner).
- Aegviidu splits into 5 models (127 of 177 frames in the largest): try loop detection or
  merging models.
- Default view frames the whole landscape on drone videos; frame the orbited object instead.

**Open questions for the owner**
- On-prem object store (decide by M5).
- Kazakh UI strings need a native speaker's review.

## 2026-10-14 — Part C (viewer) merged

- `/viewer/:runId` loads the bundle from the real API (via Vite proxy) and from the dev
  middleware. Verified with Playwright on Metal: South Building, the church video and the
  Aegviidu water tower video all load in about 1.5 s and render at 60 fps (vsync cap).
  Synthetic 5 M points: 4.9 s load, 60 fps.
- Screenshots checked by eye: build-up along the video, "seen now" highlight, camera path
  and frustums, view through the video camera matches the real frame, point → frames list.
- Fixed during review: crash on unregistered frames (panel read a field those frames do not
  have), oversized points in close-up views, frustum lines across the through-camera view.
- Known: through-camera view ignores camera roll; XYZ gizmo has no letters; CPU picking and
  no LOD (both marked `ponytail:`); Kazakh strings need a native speaker's review.

## 2026-10-14 — Part A (M0) merged

- `make up` brings up Postgres 16 + PostGIS 3.6 + pgvector 0.8 and Versity S3 Gateway, both
  healthy. `/health` returns 200 with both ok, and 503 within 0.02 s when storage is stopped.
- Runs API serves the South Building bundle; path traversal returns 404. Backend: 10 tests,
  ruff and mypy clean.
- Deviations: Postgres image is `pgvector/pgvector:0.8.0-pg16` + PostGIS from apt, because
  `postgis/postgis` has no arm64 image. Host port 5433 for Postgres. `make api API_PORT=8001`
  on this Mac, because port 8000 is taken by an unrelated container.
- Known: CI not yet run on GitHub; no S3 bucket created yet (nothing writes to S3 in M1).

## 2026-10-14 — resume after usage limit

- First A/B/C subagents stopped at their first step (model usage limit). Their worktrees had
  no changes and were removed. Relaunched A, B, C on Sonnet; this session coordinates and
  reviews before merging into `feat/m1`.
- Owner approved: the M1 plan as written; Versity S3 Gateway for development (ADR 0006).
  On-prem object store stays open until M5.
- `git ls-files data` is empty: no samples or runs are tracked.

## 2026-10-14 — M0 + M1 started

**Done**
- Research and plan: `docs/notes/m1-plan.md`; ADRs 0003–0006.
- Mac set up: FFmpeg + COLMAP (Homebrew), Python 3.12 (uv), pycolmap 4.2.1 wheel verified.
- Public test data in `data/samples/` (gitignored): South Building (COLMAP sample, with
  sparse model), three CC-licensed drone videos (two water towers, one church).
- `.gitignore` blocks video, point clouds, weights, `.env`, `data/`, `scratch/`.
- Initial commit on `main`; work continues on `feat/m1`.

**Next**
- A: infra (Postgres+PostGIS+pgvector, Versity S3), FastAPI `/health` + runs routes, CI, Makefile.
- B: pipeline — export (South Building bundle) → frames → reconstruction → three videos.
- C: viewer — synthetic bundle → South Building → video runs.

**Known issues**
- MinIO images withdrawn upstream → Versity S3 Gateway (ADR 0006).
- No CUDA on Mac → sparse clouds only in M1 (ADR 0004).

**Open questions for the owner**
- On-prem object store (decide by M5).
- No money spent; nothing paid is proposed.
