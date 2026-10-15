# devteam

Qarau's multi-agent dev team on LangGraph. Dev tooling, not part of the product.
Plan and rules: `docs/notes/devteam-plan.md`, CLAUDE.md → "Delivery".

**No human review.** Each task runs on `devteam/<task>`, becomes a PR into `dev` (the review
packet is the description) and is merged automatically only when every gate is green:
local checks, tester judge, GitHub CI, no benchmark regression, clean secret scan
(gitleaks). Otherwise it goes back to the developer (max 3 times), then `blocked`.
`dev` → `main` is a weekly release PR, merged only if CI and the full benchmark are green.

```bash
cd devteam && uv sync          # also needs: gitleaks (brew install gitleaks), gh, claude, codex
uv run devteam run             # brain plans, developers build, tester + gates decide, auto-merge
uv run devteam stop            # from any terminal: stop everything now (agents, checks, CI wait)
uv run devteam resume          # continue after stop, crash, usage limit or budget cap
uv run devteam status          # backlog with PR links / blocked reasons
uv run devteam report          # writes devteam/reports/YYYY-MM-DD.md
uv run devteam release         # dev -> main now (also runs automatically every 7 days)
uv run devteam costs           # API spend per day / agent / model
uv run devteam cleanup         # remove leftover worktrees of done/dropped tasks
uv run devteam run --fake      # whole flow with fake agents and a local fake GitHub, no cost
uv run pytest                  # 47 tests, incl. gates, crash + resume, forbidden actions
```

- Developers, in parallel: `dev_pipeline` = Claude Code (`claude -p`, subscription token in
  `.env`), `dev_app` = Codex (`codex exec`, your saved ChatGPT login; `codex login` once; no
  API key is ever passed). Codex has no network in its sandbox: `setup` installs first.
- Benchmark: developers run only the quick set (`quick_only`); the full set runs at the merge
  gate and at release, cached per `pipeline/` tree. Web tasks: the tester sees screenshots.
- Daily report: merged / blocked tasks, API cost, **subscription load per task** (rounds,
  minutes, tokens, ≈ API $), latest benchmark, last release.
- Models, budget, gates, developer folders and checks: `config.yaml`.
- Keys: `.env` (gitignored), template `.env.example`.
- Agents never push, merge or call `gh`; the orchestrator does, and refuses force-push,
  history rewrites, branch deletion and `--admin` (`graph.py` `_guard`, `gates.py`).
- `hooks/pre-push` (installed automatically): local stand-in for GitHub branch protection.
- Runtime state (checkpoints, worktrees, packets, benchmark cache, costs, event log):
  `.state/` (gitignored). Daily reports: `reports/` (gitignored, local).
