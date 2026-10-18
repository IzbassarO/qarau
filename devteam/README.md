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
uv run devteam resume          # continue after stop, crash, usage limit or budget cap
uv run devteam stop A7         # stop one task: its step is killed, it is blocked, others go on
uv run devteam resume A7       # that task back to todo; it continues on its branch
uv run devteam status          # backlog with PR links / blocked reasons
uv run devteam report          # writes devteam/reports/YYYY-MM-DD.md
uv run devteam release         # dev -> main now (also runs automatically every 7 days)
uv run devteam costs           # API spend per day / agent / model
uv run devteam cleanup         # remove leftover worktrees of done/dropped tasks
uv run devteam run --fake      # whole flow with fake agents and a local fake GitHub, no cost
uv run pytest                  # tests, incl. gates, crash + resume, forbidden actions
```

- Continuous mode (`run --continuous`, limits in `config.yaml` → `continuous`): one loop per
  developer. When a developer's task ends (merged or blocked), the brain plans the next task
  for that developer only; the others keep working. Stops when: no plannable task is left for
  any developer; the daily API budget is reached; 2 tasks in a row end blocked; `max_hours`
  (clean stop) or `max_tasks` (no new tasks, running ones finish); Ctrl+C. The first Ctrl+C
  finishes the current steps cleanly (a running developer or check is not killed); a second
  one kills everything now. A Claude Code / Codex plan limit is logged with its reset time
  (when the tool prints one), the developer waits until the reset (every 30 min when unknown,
  at most `limit_wait_max_hours`), then the same step runs again. Never automatically: a
  release to `main`, paid services (developer env gets no RunPod / cloud keys, the brain may
  not plan paid steps), edits to `devteam/` (no developer owns it; checked at start). Rounds
  that did not finish are kept in `.state/continuous.json` and resumed first by the next
  `run --continuous`. At the end it prints, and the daily report lists, every task, its
  result, the spend and why the run stopped (`.state/runs/<run>.json`). Plain `devteam run`
  is unchanged: one round, then the weekly release check.
- Dashboard (`devteam dashboard`): one local read-only page (127.0.0.1 only, GET only,
  refreshes every 20 s), real data only, no model calls: agents (status, current task, time on
  it, from `.state/events.log` of the current run only: events before run.pid was written and
  tasks the backlog says are not in progress never show as working), today's API spend vs the budget, subscription load per task,
  subscription-limit events and reset times, the queue (todo / in progress / blocked / done
  with PR links), open PRs, CI on `dev` (`gh`, cached 60 s), the latest commits, and "Needs
  your attention": red CI on `dev`, blocked tasks, PRs waiting for the owner, spend above
  80 % of the budget. Every value from `.env` is redacted.
- Supervisor (`config.yaml` → `supervisor`, owner 2026-10-17): per task, `max_tokens_per_round`,
  `max_tokens_per_task` (all rounds of the task in this run) and `max_round_min` (a round
  commits only when it ends, so this is the longest a task goes without a new commit).
  Developer tokens are counted live: Claude Code runs with `--output-format stream-json`
  (usage per API message), Codex through the `token_count` events of its session rollout file
  (`~/.codex/sessions`). Over a limit, or past `timeout_min`: the step's process group is
  killed, what the round wrote is committed (same checks), the task is `blocked` with the
  reason and what to do, and shows under "Needs your attention"; the other developers keep
  going (`devteam/supervise.py`).
- Loop detection: the tester's reason is the set of unmet criteria, failing checks and missing
  screenshots. Sent back twice in a row for the same reason: no new round, `blocked`.
- Before every round the orchestrator merges the latest `dev` into the task branch (never the
  agent, never a rebase). Conflicts: a short "resolve conflicts" step with the file list
  instead of a full round; the orchestrator concludes the merge only when no marker is left
  and nothing else changed, then a normal round follows. Conflicts found by the gates go back
  the same way; markers left after the step block the task.
- Hard scope enforcement (`devteam/scope.py`, owner 2026-10-17, after A7 shipped its backend
  as a `.patch` under docs/): the tester's first step is a scope gate on
  `git diff dev...HEAD`. Every path must be in the developer's `owns` (config: dev_app
  `web/` only; dev_pipeline `pipeline/`, `backend/`, `infra/`, `docs/`) or the task's
  `writable`; a path outside → `out_of_scope`, no retry, blocked as "needs a split". Protected
  paths (devteam/, .github/, backlog.yaml, CLAUDE.md, AGENTS.md, plus `gates.protected`:
  benchmark video set, lint configs) → hard fail. A developer that even tries to write another
  developer's code folders or a protected path is stopped the same way at the end of its
  round (its own docs/ attempts are only reverted). Anti-bypass checks on the diff: new
  `test.skip` / `.only` / `xfail` / `pytest.mark.skip` (a skip only while a data file is
  missing is allowed), fewer assertions or tests, looser thresholds in tests, `.patch` /
  `.diff` files, code under docs/ → sent back with the reason (twice → blocked). Every
  violation goes to `.state/violations.jsonl`, "Needs your attention" and the daily report.
- The tester never trusts the developer: setup, checks and screenshots run again on a fresh
  detached checkout of the committed HEAD (pipeline tasks: the tester runs the quick benchmark
  there itself and screenshots that); only the orchestrator sets a task to done, after every
  gate passed (backlog.yaml is protected).
- Plans stay inside the developer's folders: criteria that name another developer's folder
  (or devteam/) are rejected; the brain lists such tasks in `needs_split` and the orchestrator
  blocks them with the proposed split (A7 asked dev_app for backend/ and looped).
- `devteam stop <task>` / `resume <task>`: a file in `.state/control/`; a running orchestrator
  reads it every few seconds in a step, at every node and when it plans; with no run active
  the CLI applies it at once (pushed to `dev` like any status). An owner stop never counts
  toward "2 tasks in a row blocked".
- `writable: [data/runs]` on a task lets its developer add new files there (extra `--add-dir`);
  if a file that was there when the task started changes or disappears, the task is blocked.
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
  gate and at release, cached per `pipeline/` tree; a task may set its own `quick_only`.
- Screenshots: any task whose criteria mention the viewer lists `routes`; the tester
  screenshots them (pipeline tasks: the viewer on the quick benchmark output). A missing
  screenshot fails the task even if the judge passed. Pipeline tasks also get `dev-*.png`: the
  same routes on dev's cached gate benchmark, so the tester can compare with dev.
- Dependencies: a task may list `depends_on: [ids]`. The brain never plans it until all of
  them are `done`; a `blocked` task whose dependencies are all done goes back to `todo` (pushed
  to `dev` like any status) when the last one merges, or at the next `devteam run`.
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
  `devteam/backlog.yaml`. The main checkout, when on `dev`, is then fast-forwarded
  (`--autostash`, your local edits stay). Never reset `backlog.yaml` by hand.
- `devteam run` refuses to start while the backlog's status fields differ from `origin/dev`
  (it prints them; after a crash use `devteam resume`). The brain skips any task whose
  `devteam/<id>` branch is already merged into `dev`.
- A task is `done` only when this run's commits went through a PR that passed CI and was
  merged. A branch already merged with nothing new is blocked as "nothing to merge, already
  done", never marked done.
- Daily report: merged / blocked tasks, API cost, **subscription load per task** (rounds,
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
