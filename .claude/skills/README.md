# Project skills

Added by the owner's request only; agents never download or install skills themselves
(`.claude/` and `.agents/` are protected paths in Kerege). Codex reads repository skills from
`.agents/skills/`, so each skill there is a symlink to its folder here (checked with
codex-cli 0.162.1: `.claude/skills` alone is not read).

| Skill | Source | Commit | Licence | Use |
| --- | --- | --- | --- | --- |
| frontend-design | github.com/anthropics/skills, `skills/frontend-design` | `dbd4588f9e1033efb41dad4bef2f7947c8993d44` (2026-10-09) | Apache-2.0 (`LICENSE.txt`) | visual work in `web/`, after `docs/design/DESIGN.md` |

Files are byte-identical to that commit (sha256: SKILL.md `d9197063…c1d3`, LICENSE.txt
`0d542e0c…4594`). To update: fetch the new commit by hand, review the diff, update this table.
