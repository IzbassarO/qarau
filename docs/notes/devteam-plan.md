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
- Task branches `devteam/<id>` start from `base_branch` (`dev`); `devteam review` merges
  approved work into it. Never main, never pushes.
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
- v2: dev_app (`codex exec --json`, `codex exec resume`), parallel with dev_pipeline;
  `devteam review` (show packet, approve → merge into `dev`, reject → back to the brain).
