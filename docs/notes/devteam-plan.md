# Dev team on LangGraph — plan

Dev tooling in `devteam/`, not product code. Owner's brief: 2026-10-14.

## Shape

- `brain` (Opus 5.5, API) reads `devteam/backlog.yaml`, picks at most one task per developer,
  writes a plan + acceptance criteria, assigns `dev_pipeline` or `dev_app`.
- Each task runs in its own LangGraph subgraph, in parallel:
  `worktree → develop → test → (develop again, ≤ 3 fix loops) → packet`.
- The brain only plans; the whole plan is validated (unique tasks and developers, open
  tasks, task `area` allowed for the developer) and checkpointed before a separate,
  idempotent `claim` node writes the backlog.
- Developers never commit. Before each run and each commit the graph checks the worktree
  is registered and on exactly `devteam/<task>`. It stages only validated paths (status from
  a throwaway index, NUL-separated, both sides of renames), reverts edits outside the
  developer's folders (`owns`, plus `docs/`) and protected files (`.env`, `data/`,
  `scratch/`, found by snapshot because git ignores them). Developer commits are unwound and
  checked the same way.
- A task passes only if every setup step and check passes and the judge passes.
- No human review (owner's decision, 2026-10-14). Task branches `devteam/<id>` start from
  `origin/dev`. After the tester passes, the `gates` node: merges the latest `dev` in (merge,
  never rebase; conflict → blocked), scans `dev..HEAD` with gitleaks (a leak → blocked,
  never pushed; staged changes are also scanned before every commit), runs the benchmark on
  dev and on the branch when `pipeline/` changed (throwaway worktrees, cached per commit;
  regression → back to the developer; no benchmark on dev yet → skipped), pushes, opens or
  updates the PR into `dev` with the packet, waits for GitHub CI on exactly that commit and
  merges it (`--match-head-commit`, merge commit, no `--admin`). Any failure → back to the
  developer; after 3 → `blocked`.
- Weekly release (`devteam release`, due after 7 days, checked at the end of every run):
  PR `dev` → `main`, merged only if the full benchmark runs on `dev` without regression vs
  `main` and CI is green.
- The orchestrator refuses force-push, deletion, pushes other than `devteam/<task>`, rebase,
  `update-ref`, and gh `api`/`--admin`/`--delete-branch`. Branch protection on GitHub needs a
  paid plan for a private repo; `devteam/hooks/pre-push` stands in locally.
- `devteam stop` sends SIGTERM to the run; every agent/check/benchmark runs in its own
  process group and is killed with it. Finished steps are checkpointed; `resume` continues.
- Daily report `devteam/reports/YYYY-MM-DD.md`: done tasks with PR links, blocked tasks with
  reasons, cost, latest benchmark, last release.
- SQLite checkpoints (`devteam/.state/`): `devteam resume` re-runs only the node that was
  interrupted (usage limit, budget cap, crash). Subgraph progress survives too.
- Costs: every API call goes through `llm_call`, which reserves its worst-case cost in one
  SQLite transaction, refuses it if the day's cap would be crossed, and settles to the real
  usage after. Unsettled reservations (crash, unknown billing) count at the worst case.
  Subscription runs (Claude Code, Codex) are logged, not billed.
- `devteam cleanup` removes worktrees of done/dropped tasks; branches and packets stay.

## Steps

- v0: graph with fake agents, live view, resume, test — no cost.
- v1: real brain (structured output) + dev_pipeline (`claude -p`, `--resume` for fixes) +
  tester (checks from config + Sonnet judge + packet). One real task end to end.
- v2: dev_app (`codex exec --json`, `codex exec resume`), parallel with dev_pipeline.
  (`devteam review` dropped: gates replace the owner's review.) Codex runs in the
  `workspace-write` sandbox (no network, no `.git` writes): the orchestrator runs the
  developer's `setup` (`pnpm install`) before every round and commits. Web tasks: the brain
  lists `routes`, the tester gets screenshots of them (Playwright CLI on the worktree's Vite
  server with the main checkout's runs) and they are listed in the packet (local files).
- v2 limits savers (owner, 2026-10-15): developers run only the quick benchmark
  (`gates.benchmark.quick_only`: notre-dame + room, ~11 min) with the cached dev numbers in the
  brief; the full set runs only at the merge gate and at release, cached per `pipeline/` tree
  (a web merge doesn't re-run it). Mykolaiv gets wider tolerances (measured noise). The daily
  report shows subscription load per task: rounds, minutes, tokens, ≈ API $ (Claude Code).
