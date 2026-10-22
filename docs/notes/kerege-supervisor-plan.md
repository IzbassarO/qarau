# Kerege supervisor and fixes (PR 1) — plan

Owner request 2026-10-17. Today A7 used 42.6 M subscription tokens while going nowhere and P2b
17 M; one crash or limit could also stop every developer. Goal: a task that goes wrong costs a
bounded amount and stops alone, with a clear reason under "Needs your attention".

1. **Supervisor** (`config.yaml` → `supervisor`): `max_tokens_per_round`, `max_tokens_per_task`,
   `max_round_min` (a developer round commits only when it ends, so this is the longest a round may
   go without a new commit). `Team.run_proc` polls every few seconds; a developer round's watcher
   counts tokens live (Claude Code `stream-json` usage per API message; Codex `token_count` events
   in its session rollout file). Over a limit: kill the process group, commit what the round
   produced (same path/secret checks), `blocked` with the reason. Other developers keep going.
2. **Loop detection**: the tester's send-back reason = unmet criteria + failing checks + missing
   screenshots. Same reason twice in a row → `blocked`, no third round.
3. **Merge dev before every round** (orchestrator). Conflicts → a short "resolve conflicts" step
   with the file list; the orchestrator concludes the merge only if no marker is left, then a
   normal round follows.
4. **Plans stay inside the developer's folders**: `validate_plan` rejects criteria naming paths the
   assignee does not own; the brain reports such tasks as `needs_split` (proposed parts), the
   orchestrator blocks them with that proposal.
5. **A7 split**: A7a (backend ROI API, dev_pipeline) + A7 (web, resumes on `devteam/A7`,
   depends on A7a). The patch file leaves the branch; `docs/notes/roi-format.md` (the contract)
   goes to `dev` so A7a can read it.
6. **P3e** "Refresh viewer runs": a backlog field `writable: [data/runs]` lets a task add new
   run folders; the orchestrator blocks it if an existing file there changed or vanished.
7. **`devteam stop <task>` / `devteam resume <task>`**: a control file the running orchestrator
   reads (poll + node start + brain); applied directly when no run is active.
8. **Dashboard**: agent status only from events after the current run started (run.pid mtime) and
   only for tasks in progress.

PR 2 (after PR 1 merges): dashboard v2.
