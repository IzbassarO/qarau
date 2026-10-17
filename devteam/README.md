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
  it, from `.state/events.log`), today's API spend vs the budget, subscription load per task,
  subscription-limit events and reset times, the queue (todo / in progress / blocked / done
  with PR links), open PRs, CI on `dev` (`gh`, cached 60 s), the latest commits, and "Needs
  your attention": red CI on `dev`, blocked tasks, PRs waiting for the owner, spend above
  80 % of the budget. Every value from `.env` is redacted.
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
