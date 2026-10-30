# Project skills

Added by the owner's request only; agents never download or install skills themselves
(`.claude/` and `.agents/` are protected paths in Kerege). Codex and Kimi Code read repository
skills from `.agents/skills/`, so each skill there is a symlink to its folder here (checked with
codex-cli 0.162.1 and kimi-code 2.3.3: `.claude/skills` alone is not read).

Kerege names each developer's and the tester's skills in their prompt (`skills:` of the
developer or model in `devteam/config.yaml`; `skills.enabled: false` turns that off).

| Skill | Source | Commit | Licence | Kerege role |
| --- | --- | --- | --- | --- |
| frontend-design | github.com/anthropics/skills, `skills/frontend-design` | `dbd4588f9e1033efb41dad4bef2f7947c8993d44` (2026-10-09; same SKILL.md at `ca45fc4`) | Apache-2.0 (`LICENSE.txt`) | dev_app (design developer) |
| impeccable | github.com/pbakaus/impeccable, `.claude/skills/impeccable` v4.8.0, without `scripts/` | `df3a4abe7d0840d5f60324812c432e00b66a4e75` (2026-10-30) | Apache-2.0 (`LICENSE`, `NOTICE.md`: ios.md / android.md derived from MIT work) | dev_app |
| theme-factory | github.com/ComposioHQ/awesome-codex-skills, `theme-factory` | `0930e1373789d2eda449039f7ac154b33031de89` (2026-07-26) | Apache-2.0 (`LICENSE.txt`) | dev_app |
| webapp-testing | github.com/anthropics/skills, `skills/webapp-testing` | `ca45fc496a3436251f191354091d557da31c15b5` (2026-10-28) | Apache-2.0 (`LICENSE.txt`) | tester (QA, design QA) |
| test-driven-development | github.com/obra/superpowers, `skills/test-driven-development` | `2ce8aa15d05bd57213e6f746f953aafb7ded7a8b` (2026-10-21) | MIT (`LICENSE`, the repository's) | dev_pipeline, dev_core |
| receiving-code-review | github.com/obra/superpowers, `skills/receiving-code-review` | `2ce8aa15d05bd57213e6f746f953aafb7ded7a8b` (2026-10-21) | MIT (`LICENSE`, the repository's) | dev_pipeline, dev_core |

Files are byte-identical to those commits; only whole folders or whole files were left out.
impeccable's `scripts/` is left out: its launcher downloads an engine binary on first run, which
agents may not do; SKILL.md then reads PRODUCT.md / DESIGN.md directly ("Launcher unavailable").

Not installed (2026-10-30): trailofbits/skills (CC-BY-SA-4.0, share-alike: owner's call);
accesslint/claude-marketplace (no licence file, "MIT" only in README and plugin.json; its skills
run `npx -y @accesslint/*@latest`); superpowers `requesting-code-review` (dispatches a subagent,
which developers cannot) and `brainstorming` (asks a user questions; Kerege rounds have none).

To update: fetch the new commit by hand, review the diff, update this table.
