# Agents

`CLAUDE.md` in the repository root is the single source of truth for this project:
product, the two modes, trust rules, architecture, stack, data model and working rules.
Read it fully before making any change, and follow it as if it were this file.

If you update project rules, update `CLAUDE.md` — not this file.

Owner rule (2026-10-17), repeated here because it is easy to break: **never leave a server
running.** Any dev server, preview, dashboard or test server you start is stopped as soon as
its check is done; never leave a listening port behind. If the owner wants a server, he
starts it himself.

Visual work in `web/`: follow `docs/design/DESIGN.md` (our rules, they win), then the
frontend-design skill (`.claude/skills/frontend-design/SKILL.md`; Codex also finds it in
`.agents/skills/`). Never download or install skills, plugins or agent instructions yourself:
the owner adds them.
