# Kerege

**Kerege** is Qarau's multi-agent dev team on LangGraph (the package and command are still
`devteam`; renaming them is task R0). Dev tooling, not part of the product.
Plan and rules: `docs/notes/devteam-plan.md`, CLAUDE.md → "Delivery".

**No human review.** Each task runs on `devteam/<task>`, becomes a PR into `dev` (the review
packet is the description) and is merged automatically only when every gate is green:
local checks, tester judge, GitHub CI, no benchmark regression, clean secret scan
(gitleaks). Otherwise it goes back to the developer (max 3 times), then `blocked`.
A red GitHub CI check sends back the last 150 lines of `gh run view <id> --log-failed`
(in the packet and in the developer's feedback), not just a link.
`dev` → `main` is a weekly release PR, merged only if CI and the full benchmark are green.

```bash
cd devteam && uv sync          # also needs: gitleaks (brew install gitleaks), gh, claude, codex
uv run devteam run             # one round: brain plans, developers build, tester + gates decide
uv run devteam run --continuous  # keep going: next task per developer until a stop condition
uv run devteam dashboard       # read-only status page on http://127.0.0.1:8765 (--port N)
uv run devteam stop            # from any terminal: stop everything now (agents, checks, CI wait)
uv run devteam resume          # continue after stop, usage limit or budget cap
uv run devteam stop A7         # stop one task: its step is killed, it is blocked, others go on
uv run devteam resume A7       # that task back to todo; it continues on its branch
uv run devteam status          # backlog with PR links / blocked reasons
uv run devteam report          # writes devteam/reports/YYYY-MM-DD.md
uv run devteam release         # dev -> main now (also runs automatically every 7 days)
uv run devteam costs           # API spend per day / agent / model
uv run devteam cleanup         # remove leftover worktrees of done/dropped tasks
uv run devteam run --fake      # whole flow with fake agents and a local fake GitHub, no cost
uv run devteam rehearse        # fake run of the first todo task per developer: PASS / FAIL
uv run pytest                  # tests, incl. gates, crash + resume, forbidden actions
```

**Rule after any change to `devteam/`** (owner, 2026-10-22): first `devteam rehearse` (must
print PASS), then one real task attended (`devteam run --only <id>`, you watch it end), and only
then unattended (`run --continuous` overnight).

- Watchdog (owner, 2026-10-22: Kerege crashed at 20:29, the owner found out at 01:50;
  `devteam/watchdog.py`): `run` and `resume` start the orchestrator as a child process. A
  finished run notifies one line (done / blocked / API $, and why it stopped); a blocked task
  notifies too (not an owner's own `devteam stop <task>`). Ctrl+C, `devteam stop`, the budget cap,
  a subscription limit and a refused start (backlog drift, nothing to resume) are not crashes.
  Anything else (an unhandled exception in the orchestrator itself, or the process killed):
  `.state/last_crash.json` (error, file:line, traceback, exit code, attempt), a notification,
  the steps it left running are killed (`.state/steps/`), and after 60 s the watchdog runs
  `devteam resume` (continuous: `run --continuous`, which resumes its pending rounds) once.
  If the resume crashes (or refuses to start) within 30 min, it stops and notifies "needs
  you"; a resume that ran longer was healthy, so its crash gets one resume again.
  Ctrl+C or `devteam stop` during the
  60 s cancels the resume. Notifications: a macOS banner (`osascript`), plus an email when
  `DEVTEAM_NOTIFY_EMAIL` is set in `devteam/.env` (SMTP: `DEVTEAM_SMTP_HOST`, `_PORT` (587
  STARTTLS, 465 SSL), `_USER`, `_PASSWORD`, optional `DEVTEAM_NOTIFY_FROM`); without it the
  email is skipped and the watchdog says so at start. Every notification is logged with what was
  sent in `.state/notifications.log`. A crash in a continuous developer loop itself (not in a
  round) halts the run and counts as a crash. Not covered: the watchdog process itself being
  killed (with its terminal, or `kill -9`): the heartbeat shows it.
- Heartbeat: the orchestrator writes `.state/heartbeat` every 30 s. The dashboard's Run card
  shows "last heartbeat N s ago", red with an item under "Needs your attention" when it is over
  5 min while a run is active (or its `run.pid` was left behind); a crash not followed by a
  newer heartbeat is listed there too.
- `devteam rehearse`: on a fresh fake state (`.state/fake` emptied, the fake backlog copied from
  `backlog.yaml`, the fake remote seeded from `origin/dev`, `devteam-fake/*` branches removed) it
  runs `devteam run --fake --only <first plannable todo task of each developer>` through the
  watchdog. PASS: exit 0, every task `done`, no crash recorded (a crash the resume recovered
  from is still a FAIL). When nothing in `backlog.yaml` is plannable (2026-10-23: every todo
  task waited on S1, DEF1-api or U2, blocked by limits) it rehearses on `tests/backlog.yaml`
  (T-001, T-002) and says so; fake agents never read a task's text anyway. Fake agents and the fake tester (no lint or tests run), real scope /
  secret gates, merges and release on the fake remote: well under a minute, costs nothing.
- A fake run never changes the real repo outside `devteam-fake/*`: its release tag goes to the
  fake remote only (2026-10-22: `release-YYYY-MM-DD.2` landed in the shared `refs/tags`).
- Continuous mode (`run --continuous`, limits in `config.yaml` → `continuous`): one loop per
  developer. When a developer's task ends (merged or blocked), the brain plans the next task
  for that developer only; the others keep working. Stops when no developer can take a task
  (nothing plannable, or paused after 2 blocked in a row, see below); the API budget
  (`daily_budget_usd`, counted over the last 24 h, not reset at midnight) is reached (a
  reservation left open over 2 h, e.g. by a killed run, is marked `stale` on the next
  run / resume / costs / report: still counted at its worst case, shown as "billing unknown"); `max_hours` (clean stop) or `max_tasks` (no new tasks, running ones finish); Ctrl+C. The first Ctrl+C
  finishes the current steps cleanly (a running developer or check is not killed); a second
  one kills everything now. A Claude Code / Codex plan limit is logged with its reset time
  (when the tool prints one), the developer waits until the reset (every 30 min when unknown,
  at most `limit_wait_max_hours`), then the same step runs again. Never automatically: a
  release to `main`, paid services (developer env gets no RunPod / cloud keys, the brain may
  not plan paid steps), edits to `devteam/` (no developer owns it; checked at start). Rounds
  that did not finish are kept in `.state/continuous.json` and resumed first by the next
  `run --continuous`. At the end it prints, and the daily report lists, every task, its
  result, the spend and why the run stopped (`.state/runs/<run>.json`); a run past midnight
  rewrites the report of every day it touched. Plain `devteam run`
  is unchanged: one round, then the weekly release check.
- Dashboard (`devteam dashboard`, v2): one local read-only page (127.0.0.1 only, GET only,
  refreshes every 20 s keeping the open tab), real data only, no model calls, no network
  assets (system fonts, inline CSS/JS: works offline). Summary row: run state and stop reason,
  tasks done today, API spend of the last 24 h vs budget, subscription tokens today, time running. "Needs your
  attention" comes first and says what to do for each item (red CI, blocked tasks except those
  only waiting for dependencies, PRs waiting for you, spend above 80 %, scope/bypass
  violations). Agents as cards (status colour, task, time, last message, tool, tokens today),
  the queue as columns, today's timeline per agent (working / waiting stretches from the
  events log), subscription tokens per task as bars with the supervisor limit, limit events
  with a live reset countdown, violations, API cost with prompt-cache hits, PRs, CI, commits.
  **Office** tab: one room per department (Lead = brain, Core = dev_pipeline, App = dev_app,
  Testing = tester); each agent is a figure at a desk whose look shows the real state
  (working at a lit screen, waiting with the reset time, blocked with a red sign, idle);
  click it for task, time, last messages and tokens today. Agent status comes only from the
  current run's events (after run.pid was written) and never for a task the backlog says is
  not in progress. Every value from `.env` is redacted.
- Supervisor (`config.yaml` → `supervisor`, owner 2026-10-17): per task, `max_tokens_per_round`,
  `max_tokens_per_task` (all rounds of the task in this run), `max_idle_min` (no output from
  the developer and no CPU work in its processes: long COLMAP, benchmark or e2e work is
  progress, CPU sampled every minute) and `max_round_min` (the longest round). A task may set
  its own in backlog.yaml, e.g. P3e `supervisor: {max_round_min: 180}` (2026-10-18: P3e was
  killed at 60 min with its refresh done). Developer tokens are counted live: Claude Code runs
  with `--output-format stream-json` (usage per API message; a killed round's output tokens
  come from its session transcript in `~/.claude/projects`, the stream has only the count at
  each message's start), Codex through the `token_count` events of its session rollout file
  (`~/.codex/sessions`). Over a limit, or past `timeout_min`: the step's process group is
  killed, what the round wrote is committed (same checks), the task is `blocked` with the
  reason and what to do, and shows under "Needs your attention"; the other developers keep
  going (`devteam/supervise.py`). Limits since 2026-10-23 (owner): 16 M per round, 36 M per
  task, 90 min per round; the backlog of #66 has fewer, 2-3x bigger tasks (S1 and DEF1-api hit
  the old 8 M per round, U2 the old 12 M per task while otherwise green).
- Parts (owner, 2026-10-23): a task whose goal has more than ~4 build items is planned by the
  brain as 2-3 parts inside the same task (`parts` in the plan, never new backlog tasks), each
  with its own plan, acceptance criteria and routes; the task's top-level criteria (Owner
  additions, "ruff, mypy, pytest pass") are checked in the last part. The developer's brief says
  which part it is on, which parts are done and accepted (on the branch, not to be redone) and
  which come later; the tester judges only the current part's criteria. A part accepted by the
  tester starts the next one in a fresh round (new session, full brief); send-backs
  (`max_fix_loops`) and loop detection count per part; the gates, PR and merge come once, after
  the last part. The packet lists the parts with the accepted ones ticked. Accepted parts are
  kept in `.state/parts/<task>.json` (stage A, 2026-10-27; DS1 re-planned an accepted part 1 on
  resume): a resumed task plans only the remaining parts, unless its goal changed (then the
  whole task again, said in the events log); merged, the file goes.
- Task size (stage A, 2026-10-27; DS1 got 16 criteria in 2 parts for 5 in its entry): at most 8
  criteria per part, 5 in part 1 of several; a goal with "Done when:" lists N criteria (its
  `;`-clauses plus Owner additions) and the plan has at most N + 3, each extra one in
  `added_criteria` with why (shown in the plan). More than 3 parts: `needs_split`, as is a task
  still too big after the one re-plan.
- Tester verdicts are complete or they do not pass (stage A): `criteria` must give each number
  1..N of the part's criteria exactly once. Otherwise the tester is asked once more with what
  was missing, repeated or out of range; still incomplete, the round does not pass, it is
  logged in `.state/tester_errors.jsonl` and not counted against the developer (twice in a row
  ends the task). The judged diff leaves out lockfiles and generated files (`uv.lock`,
  `pnpm-lock.yaml`, `package-lock.json`, `yarn.lock`, `*.min.js`, `*.map`; still in `--stat`);
  over 120 000 characters it is not judged and cannot pass ("too large to review").
- A check that fails while the machine slept (wall clock over 60 s ahead of the monotonic one,
  `run_proc`) runs once more, and the events log says so; watched developer runs never do.
- Loop detection: the tester's reason is the set of unmet criteria, failing checks and missing
  screenshots. Sent back twice in a row for the same reason: no new round, `blocked`.
- Tester send-backs name the exact place (owner, 2026-10-23: U2 was sent back three times on
  small screenshot points): for every unmet criterion the feedback gives the file and line in
  the diff, or the component with its CSS selector / i18n key, and what it must become; for
  cosmetic points seen only in screenshots (truncated text, a chip on legacy rows) also which
  screenshot and element, so the developer fixes it in one round.
- Before every round the orchestrator merges the latest `dev` into the task branch (never the
  agent, never a rebase). Conflicts: a short "resolve conflicts" step with the file list
  instead of a full round; the orchestrator concludes the merge only when no marker is left
  and nothing else changed, then a normal round follows. Conflicts found by the gates go back
  the same way; markers left after the step block the task.
- Hard scope enforcement (`devteam/scope.py`, owner 2026-10-17, after A7 shipped its backend
  as a `.patch` under docs/): the tester's first step is a scope gate on
  `git diff dev...HEAD`. Every path must be in the developer's `owns` (config: dev_app
  `web/` plus markdown only in `docs/decisions/` and `docs/notes/` — entries may be patterns
  like `docs/notes/*.md`; dev_pipeline `pipeline/`, `backend/`, `infra/`, `docs/`) or the task's
  `writable`; a path outside → `out_of_scope`, no retry, blocked as "needs a split". Protected
  paths (devteam/, .github/, backlog.yaml, CLAUDE.md, AGENTS.md, plus `gates.protected`:
  benchmark video set, lint configs, `docs/design/` = design system v8) → hard fail. A developer that even tries to write another
  developer's code folders or a protected path is stopped the same way at the end of its
  round (its own docs/ attempts are only reverted). Anti-bypass checks on the diff: new
  `test.skip` / `.only` / `xfail` / `pytest.mark.skip` (a skip only while a data file is
  missing is allowed), fewer assertions or tests, looser thresholds in tests, `.patch` /
  `.diff` files, code under docs/, test-runner retries set in code or test config (Playwright
  `retries:` in its config or `describe.configure({ retries })`, `--retries`, `jest.retryTimes`,
  Vitest `retry:` in its config or test options, `--reruns` / `reruns=` /
  `pytest.mark.flaky`; never a library's own option like React Query's `retry: false`). The JS
  helpers (`xit`, `fit`, `fdescribe`, `xtest`, `xdescribe`, `.only`, `.skip`) count only in
  .ts/.tsx/.js/.jsx/.mjs/.cjs test files → sent back with the reason (twice → blocked). Every
  violation goes to `.state/violations.jsonl`, "Needs your attention" and the daily report.
- Owner-approved test rewrites (`gates.test_rewrite_allowed`, per task; owner 2026-10-24 for
  DS1: tests that pin the old look move to the v8 values): in the listed test files, fewer
  assertions and moved thresholds are not bypass findings for that task, and the tester gets
  `<owner_approved_test_rewrites>` so it does not call them weakened. A skip or `.only` there is
  still refused; other files are checked as always.
- Test files between rounds (owner 2026-10-21; UX1b round 2 swapped its new e2e's fixed date
  for one the browser computed): after a send-back, the branch's test files are diffed against
  the HEAD the tester last judged. Fewer assertions there is a bypass finding; the judge also
  gets that diff and answers `tests_weakened` (an assertion removed or loosened, or an expected
  value now computed by the code under test), which fails the round. A weakened round keeps
  the old base, so resubmitting it unchanged is the same reason twice: blocked. The dev, tester
  and brain prompts say: expected values come from fixtures, never from the code under test.
- The tester never trusts the developer: setup, checks and screenshots run again on a fresh
  detached checkout of the committed HEAD (pipeline tasks: the tester runs the quick benchmark
  there itself and screenshots that); only the orchestrator sets a task to done, after every
  gate passed (backlog.yaml is protected). A branch that changes web/ also gets the Playwright
  e2e run there (`gates.e2e`), by Kerege first, right after the round: a fresh Vite on a free
  port (`QARAU_E2E_PORT`, never a reused server), killed with the step after. A failing e2e
  sends the round back with the failures and no LLM tester call; otherwise the judge sees every
  test line (passed / failed / skipped). Developers never run e2e: a Codex sandbox cannot open
  a port (the sandbox is not loosened for it). Owner 2026-10-27: fixed `--workers=3
  --retries=1` (late failures were flakes under load, 24/24 passed on a re-run). A test that
  fails, then passes on the retry, is flaky: it does not block, but it is listed in the packet
  (= PR body), in `.state/flaky.jsonl` and in the daily report. A test that fails twice blocks;
  the developer gets its error lines and the first 30 lines of its `error-context.md`.
- Owner additions: every `- ` line under "Owner additions (<date>):" in a task's goal must be
  one of the plan's criteria, word for word (case and line breaks aside), or the plan is
  rejected and re-planned once.
- `owner_only: true` (P4a-2a-run): the brain never plans the task and the dashboard never
  suggests `devteam resume` for it; the owner does it in an interactive Claude Code session.
- Plans stay inside the developer's folders: criteria that name another developer's folder
  (or devteam/) are rejected; the brain lists such tasks in `needs_split` and the orchestrator
  blocks them with the proposed split (A7 asked dev_app for backend/ and looped).
- `devteam stop <task>` / `resume <task>`: a file in `.state/control/`; a running orchestrator
  reads it every few seconds in a step, at every node and when it plans; with no run active
  the CLI applies it at once (pushed to `dev` like any status). An owner stop never counts
  toward "2 tasks in a row blocked".
- A developer whose 2 tasks in a row end blocked pauses for the rest of the run (it is said in
  the events and the stop reason); the others keep going, and the run ends only when no
  developer can take a task.
- A crash (any error in a task's step) blocks only that task, with `CRASHED in <step> at
  <file:line>: <error>` as its reason; the other tasks keep running and the run ends normally
  (2026-10-21: UX-dense's tester crash stopped the whole run). A crash in a developer round
  first checks the round's work like a halted round (forbidden commits unwound). Fix the
  cause, then `devteam resume <task>`. A crash outside a task (planning) pauses that developer
  in continuous mode; its round stays in `.state/continuous.json` and the next run resumes it.
- Subprocess output and git output are decoded as UTF-8 with replacement, never
  `surrogateescape`: a lone surrogate (a binary file in a diff) can't be written to the events,
  the backlog, a packet, the checkpoint or the API (2026-10-21, UX-dense's `dense.color.u8`).
- `claude` / `codex` are resolved to absolute paths at run start and checked before every
  round; a missing one is looked up on PATH again, then once more after 30 s, then the round
  crashes (2026-10-18 23:39: `claude` vanished in a failed self-update and stopped the run).
- `writable: [data/runs]` on a task lets its developer add new files there (extra `--add-dir`);
  if a file that was there when the task started changes or disappears, the task is blocked.
- Runs dir: `config.yaml` → `runs_dir` (default `data/runs` of the main checkout) is the one
  place viewer runs live. Every developer gets it as `QARAU_RUNS_DIR`; commands use
  `{runs_dir}` (e2e, dev_app screenshots). A task with `runs_dir` in its `writable` (P3e) is
  screenshot on that directory, with no quick benchmark and no `dev-*` comparison.
- Prompt caching: the brain's and tester's system prompts and their stable context (CLAUDE.md,
  plans, benchmark notes, file list; the task, plan and criteria) are cached with the 1-hour
  TTL; the costs log records cache reads and writes, priced with `price_cache_read` (writes
  2x input), and the dashboard and report show the cache hit share per agent.
- No leftover servers (owner rule): every developer and tester step runs in its own process
  group with `KEREGE_STEP=<id>` in its environment (`devteam/procs.py`). When the step ends
  (success, failure, timeout, Ctrl+C), everything in the group or carrying the marker is
  killed, and each TCP port it still listened on is logged as a warning (`left port N
  listening`). Output goes to files, so a background server can't hold the step open until
  its timeout. Screenshots refuse to run when the URL already answers before Kerege starts its
  own dev server (a stale server), and stop the server afterwards.
- Developers, in parallel: `dev_pipeline` = Claude Code (`claude -p`, subscription token in
  `.env`), `dev_app` = Codex (`codex exec`, your saved ChatGPT login; `codex login` once; no
  API key is ever passed). Codex has no network in its sandbox: `setup` installs first.
- Benchmark: developers run only the quick set (`quick_only`); the full set runs at the merge
  gate and at release, cached per `pipeline/` tree; a task may set its own `quick_only`. Only
  a clean result is cached as final: a run where a gated video failed, or one the gate found
  regressed, is measured again next time. A regression is re-measured once on the regressed
  videos only (`--only`); the task goes back only if it repeats, and the packet shows both
  attempts. After a benchmark send-back the packet warns when `pipeline/` then changed only in
  comments or whitespace.
- Screenshots: a web task whose criteria mention the viewer or screenshots lists `routes`; any
  task whose criteria name an app page path (`/viewer/<run>`, `/inspections`; never an API route
  like `/api/...` or a file like `/openapi.json`) lists it too. A backend task may say "the
  viewer must not break" without routes (B1, S1 and DEF1-api lost their first plan to that
  check, 2026-10-23). The tester
  screenshots them (pipeline tasks: the viewer on the quick benchmark output) at every
  `screenshots.viewports` (default 1440x900 and 390x844) and, for dev_app, in both
  `themes` (light, dark: emulated `prefers-color-scheme`) and `locales` (ru, kk, en: the app's
  `qarau.lang` set before any page script; every viewport x theme in ru, every viewport in the
  first theme in kk and en; `dev-*` in ru only), as
  `<route>-<viewport>[-<theme>][-<locale>].png` in `.state/packets/<task>/`, emptied at the
  start of each round. Screenshots over the API request limit (90 images, 24 MB; `dev-*` go
  first) are named to the tester as not attached, never silently cut. A missing screenshot fails
  the task even if the judge passed. `dev-*.png` are the "before": pipeline
  tasks, the same routes on dev's cached gate benchmark; web tasks, the same routes built from a
  fresh checkout of dev. The tester signs in first (`devteam/shoot.mjs`, A1b login) with
  `QARAU_TEST_USER` / `QARAU_TEST_PASSWORD` from `.env` (passed in the environment, never
  printed; missing -> a warning, the page is shot as it is); `"/?signed-out"` shows the login
  page itself. Plan check: a `/viewer/<run>` route shows the task's own output, i.e. a quick
  benchmark video, or for a task that writes runs a run not in `runs_before_task` (the runs
  dir at the task's start; the snapshot is kept until the task is done).
  Web pages are shot against the real API (owner, 2026-10-23: U2's tester saw "could not load
  sites" / "Status unavailable" because Vite alone serves only `/api/runs`): `screenshots.api`
  of dev_app starts `backend/` of the same checkout on a fresh SQLite file
  (`.state/api/<task>.sqlite`: `alembic upgrade head`, then `python -m app.seed` with org "dev"
  and users admin / engineer / viewer; no sites) on a free port, waits until `/api/runs`
  answers, then starts Vite with `QARAU_API_PROXY` pointing at it; `QARAU_API_TOKEN` is empty
  (open dev mode), so the tester's login works with any password. Both servers are killed with
  the step. An API that fails to start is a named screenshot problem (the task fails), never a
  page shot without it. Pipeline tasks keep Vite's own `/api/runs` middleware.
- Loop detection compares failing criteria by their number in the plan (the judge returns it),
  never by the judge's wording (P3e was sent back 3 times for one criterion, 2026-10-19).
- Skills: `.claude/skills/` (pinned, licence checked, `.claude/skills/README.md`); Codex reads
  them through symlinks in `.agents/skills/`. Both are protected; agents never download skills.
  Web developers are told that design system v8 (`docs/design/DESIGN.md`, the reference screens
  in `docs/design/screens/`) is the UI's only source of truth (owner, 2026-10-24): never invent
  or change colours, fonts, components or layout; only alignment, spacing, size and responsive
  behaviour. Reference screens named in a task's goal, plan or criteria
  (`docs/design/screens/<name>.png`) are attached to the tester after its own screenshots and
  before `dev-*`; it fails a different colour, font or component and allows spacing or size
  differences. Naming a reference screen in a criterion is read-only: no split for it.
- Dependencies: a task may list `depends_on: [ids]`. The brain never plans it until all of
  them are `done`; a `blocked` task parked on them (`blocked_reason: waits for <id>`, naming
  one of its `depends_on`) goes back to `todo` (pushed to `dev` like any status) when the last
  one merges, or at the next `devteam run`. A task blocked for any other reason (fix loops,
  gates, limits, a crash, the owner) stays `blocked` until `devteam resume` (owner, 2026-10-27).
  Status `later` (owner, 2026-10-22) parks a task off the demo chain: the brain never plans it,
  done dependencies never move it to `todo`, `devteam resume` leaves it; it comes back only by a
  PR into `dev` that sets `todo`. The dashboard lists them in a "Later" column.
- Resume: a task whose `devteam/<id>` branch already exists (local or on the remote) and has
  commits `dev` lacks continues on that branch, with `dev` merged in (never rebased); the plan
  says so.
- Scratch: each task gets `.state/scratch/<id>/` outside its worktree for experiments and temp
  output; files a developer leaves outside its folders are reverted (the log shows only the
  count and top-level folders).
- Task status lives in `backlog.yaml` on `dev`, owned by devteam. When a task ends (PR merged
  → `done`, or `blocked`), the orchestrator commits its status fields (`status, owner, run,
  branch, pr, done_at`, `blocked_reason`) straight to `dev` as `backlog: P3 done (#18)`:
  built on a fresh `origin/dev`, retried on a race, never forced, never to `main`. The
  pre-push hook lets a push to `dev` through only if it fast-forwards and changes nothing but
  `devteam/backlog.yaml`. The main checkout, when on `dev`, is then fast-forwarded to the
  commit just pushed (`--autostash`, your local edits stay), so no status stays uncommitted
  there; claiming a task (`in_progress`) is pushed the same way. Never reset `backlog.yaml` by
  hand.
- A crash right after the orchestrator merged `dev` into a task branch (before the step was
  checkpointed) is recognised on resume: a fast-forward or a clean merge of `dev` (its tree
  equals git's own merge) is accepted as the orchestrator's, so dev's changes are never
  counted as the developer's (P3e, 2026-10-18: "protected: backlog.yaml").
- A status commit never reaches CI untested: before the push, `gates.backlog_check` runs the
  `backlog`-marked tests on the backlog it would push (`DEVTEAM_BACKLOG`). If they fail, nothing
  is pushed, the status stays local (shown as drift) and a warning names the failing test
  (2026-10-18: `backlog: A9-fix done (#41)` turned dev CI red). Backlog tests check structure
  and order, never a status Kerege writes.
- `devteam run` refuses to start while the backlog's status fields differ from `origin/dev`
  (it prints them; after a crash use `devteam resume`). The brain skips any task whose
  `devteam/<id>` branch is already merged into `dev`.
- A task is `done` only when this run's commits went through a PR that passed CI and was
  merged. A branch already merged with nothing new is blocked as "nothing to merge, already
  done", never marked done.
- Daily report: metrics over the last 7 days (tasks done, parts accepted on the first round,
  rounds and API $ per done task, blocked, tester errors, flaky tests with counts; also on the
  dashboard), merged / blocked tasks, API cost, **subscription load per task** (rounds,
  minutes, tokens, ≈ API $), latest benchmark, last release.
- Models, budget, gates, developer folders and checks: `config.yaml`.
- Keys: `.env` (gitignored), template `.env.example`.
- Agents never push, merge or call `gh`; the orchestrator does, and refuses force-push,
  history rewrites, branch deletion and `--admin` (`graph.py` `_guard`, `gates.py`).
- `hooks/pre-push` (installed automatically): local stand-in for GitHub branch protection
  (no GitHub Pro); also refuses moving an existing tag.
- Every successful release is tagged `release-YYYY-MM-DD` on `main` and pushed. Roll back:
  `git checkout release-YYYY-MM-DD` to look; to restore `main`, open a PR that reverts to it
  (`git revert` of the release merge), never a force-push.
- Runtime state (checkpoints, worktrees, packets, benchmark cache, costs, event log):
  `.state/` (gitignored). Daily reports: `reports/` (gitignored, local).
