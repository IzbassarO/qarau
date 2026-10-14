# Dev team on LangGraph — plan

Dev tooling in `devteam/`, not product code. Owner's brief: 2026-10-14.

## Shape

- `brain` (Opus 5.5, API) reads `devteam/backlog.yaml`, picks at most one task per developer,
  writes a plan + acceptance criteria, assigns `dev_pipeline` or `dev_app`.
- Each task runs in its own LangGraph subgraph, in parallel:
  `worktree → develop → test → (develop again, ≤ 3 fix loops) → packet`.
- Developers never commit; the graph commits each round and reverts edits outside the
  developer's folders (`owns` in `config.yaml`, plus `docs/`).
- Task branches `devteam/<id>` start from `base_branch` (`dev`); `devteam review` merges
  approved work into it. Never main, never pushes.
- SQLite checkpoints (`devteam/.state/`): `devteam resume` re-runs only the node that was
  interrupted (usage limit, budget cap, crash). Subgraph progress survives too.
- Costs: every API call is logged in `costs.sqlite`; the call is refused once today's billed
  spend reaches `daily_budget_usd`. Subscription runs (Claude Code, Codex) are logged, not billed.

## Steps

- v0: graph with fake agents, live view, resume, test — no cost.
- v1: real brain (structured output) + dev_pipeline (`claude -p`, `--resume` for fixes) +
  tester (checks from config + Sonnet judge + packet). One real task end to end.
- v2: dev_app (`codex exec --json`, `codex exec resume`), parallel with dev_pipeline;
  `devteam review` (show packet, approve → merge into `dev`, reject → back to the brain).
