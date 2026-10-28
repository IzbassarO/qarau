# 0033. Kerege providers; Kimi Code CLI as dev_app, behind a key proxy and a sandbox

Date: 2026-10-28. Status: accepted (Kerege v3 K1, owner decision 2026-10-28).

## Context

The owner chose Kimi K3.1 as the web (`web/`) developer, on the Kimi Open Platform key
(`MOONSHOT_API_KEY`, pay per use, model `kimi-k3.1-pro`; the Kimi Code plan's `k3.1-pro` is
another product and key), with Codex as the fallback and caps of $5 per day and $50 per month.
Kerege v2 must stay the working fallback: everything new behind config switches.

Options for running Kimi as a developer:

- **Kimi Code CLI** (`@moonshot-ai/kimi-code`, MIT, 2.3.3): an agent loop with file and shell
  tools, `kimi -p ... --output-format stream-json` (JSON lines: assistant messages, tool calls
  and results, a final `session.resume_hint` with the session id; `--session <id>` resumes).
  Its config takes any OpenAI-compatible `base_url`. It has **no sandbox**, its shell tool
  inherits the CLI's environment, and stream-json carries no token usage.
- **Codex CLI or Claude Code on a Moonshot endpoint**: Codex speaks the Responses API, Claude
  Code the Anthropic Messages API; both would depend on Moonshot's compatibility layers for
  every tool call. Not the owner's choice and harder to keep working.
- **Our own agent loop on the API**: a second agent harness to maintain.

## Decision

Kimi Code CLI, pinned (2.3.3, published 2026-10-26; 2.4.0 came out the day of this ADR), made
safe from outside, in `devteam/kimi.py`:

- **The key never enters the CLI's process.** Each round starts a proxy on 127.0.0.1 that adds
  `Authorization: Bearer $MOONSHOT_API_KEY` and forwards to `https://api.moonshot.ai`. The CLI's
  own `config.toml` (in `.state/kimi/<task>`, its `KIMI_CODE_HOME`) names the proxy and a dummy
  key. So a shell command of the agent cannot read the key from its environment or a file.
- **The proxy is the meter and the cap.** Each call reserves its worst case (request bytes at
  the input price, `max_tokens` or 32 000 at the output price; `max_context_size` 262 144 bounds
  the input) against both Kimi caps in the costs log, then settles to the `usage` Moonshot sends
  on the last stream chunk (cache reads at `price_cache_read`). Over a cap the call gets HTTP 403,
  the CLI fails and the round falls back to Codex. A call cut mid-stream stays at its worst case
  (billing unknown), as the Anthropic calls do. The supervisor reads the proxy's token totals
  live. Every cost row now names its provider; `daily_budget_usd` counts Anthropic's only.
- **macOS `sandbox-exec` (Seatbelt)** around the CLI and all it starts: writes only in the
  worktree (not its `.git`), the task's scratch folder, its own state, temp folders; no reads of
  file contents in the home folder except those and the repository's `.git` (so not the main
  checkout with `devteam/.env` and `data/`, not `~/.ssh`, `~/.codex`, `~/.claude`); network only
  to localhost (the proxy). `HOME` is an empty folder of its own and the environment keeps only
  PATH, locale and shell variables. Without `sandbox-exec` (not macOS) Kimi is not used.
- **Fallback** (one events line `kimi_cli -> codex_cli: <why>`): Kimi off, no key, no `kimi` or
  `sandbox-exec`, a task with `writable` data, a cap reached, or the CLI failing or hitting a
  limit (then the same round on what Kimi left). Sessions are tagged `kimi:<id>` so a resume
  never hands one tool's session to the other.
- **Providers** in `config.yaml`: roles and developers name `provider` (`anthropic`,
  `claude_code`, `codex_cli`, `kimi_cli`; `openai_compatible` is named but not built), and
  `providers.<name>` holds the on/off switch, key, caps and prices.

## Licences and terms

- Kimi Code CLI: MIT (github.com/MoonshotAI/kimi-code), commercial use allowed.
- Kimi Open Platform terms (platform.kimi.ai, last updated 2026-07-30): Moonshot does not
  claim ownership of content; integrating the service into own products is allowed, so the
  generated code is ours to use commercially. Moonshot may use customer content to "provide,
  maintain, develop, support, and improve the Services"; there is no self-serve opt-out (an
  enterprise agreement is needed). So what dev_app sends (the `web/` code and the task brief)
  may be used for training: no keys, customer data or `data/` is ever sent (sandbox above).
  Singapore law; no building of products that compete with the service (not our case).

## Consequences

- dev_app costs money per call (about $2 / $10 per 1M tokens in / out, $0.2 cached), bounded by
  the caps; Codex stays free on the ChatGPT plan and takes over at a cap.
- The sandbox relies on `sandbox-exec`, deprecated by Apple but used by Codex and others; if a
  macOS release removes it, Kimi is skipped and dev_app is Codex (Kerege v2).
- Linux (CI) never runs Kimi; its tests run there with a pass-through stand-in, and the sandbox
  test runs on macOS only.
