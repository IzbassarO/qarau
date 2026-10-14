# devteam

Qarau's multi-agent dev team on LangGraph. Dev tooling, not part of the product.
Plan: `docs/notes/devteam-plan.md`.

```bash
cd devteam && uv sync
uv run devteam run --fake     # whole team with fake agents, no cost, separate state
uv run devteam run            # real team: brain plans, developers build, tester checks
uv run devteam resume         # continue after a crash, Ctrl-C, usage limit or budget cap
uv run devteam status         # backlog
uv run devteam costs          # API spend per day / agent / model
uv run pytest                 # fake end-to-end run incl. crash + resume
```

- Models, budget, developer folders and checks: `config.yaml`.
- Keys: `.env` (gitignored), template `.env.example`.
- Runtime state (checkpoints, worktrees, review packets, costs, event log): `.state/` (gitignored).
