"""Real agents. Every pay-per-use model call goes through llm_call (budget + cost log)."""

import json
import os

import anthropic
import yaml

from devteam import costs


def llm_call(
    team,
    role: str,
    task: str,
    system: str,
    prompt: str,
    schema: dict,
    max_tokens: int = 16000,
    client=None,
) -> dict:
    """One structured-output call for `role` (a key of config.models). Reserves the
    worst-case cost first, settles to the real usage after. No SDK retries: a retry is a new
    call with its own reservation (`devteam resume` re-runs the node)."""
    m = team.cfg["models"][role]
    if m["provider"] != "anthropic":
        raise NotImplementedError(f"provider {m['provider']!r} for {role}: only anthropic so far")
    # Every token is at least one byte, so UTF-8 bytes bound the input tokens from above.
    est_in = len((system + prompt + json.dumps(schema)).encode()) + 1000
    max_usd = (est_in * m["price_in"] + max_tokens * m["price_out"]) / 1e6
    rid = costs.reserve(team.home, team.cfg["daily_budget_usd"], role, m["model"], task, max_usd)
    if client is None:
        if not team.secrets.get("ANTHROPIC_API_KEY"):
            costs.release(team.home, rid)
            raise RuntimeError("ANTHROPIC_API_KEY is missing in devteam/.env")
        client = anthropic.Anthropic(api_key=team.secrets["ANTHROPIC_API_KEY"])
    if hasattr(client, "with_options"):
        client = client.with_options(max_retries=0)  # one reservation = one request
    try:
        with client.messages.stream(  # streaming: long thinking can't hit HTTP timeouts
            model=m["model"],
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            output_config={
                "effort": m["effort"],
                "format": {"type": "json_schema", "schema": schema},
            },
        ) as stream:
            r = stream.get_final_message()
    except anthropic.APIStatusError as e:
        if e.status_code < 500:  # rejected before any generation: nothing billed
            costs.release(team.home, rid)
        raise  # 5xx / connection / timeout: billing unknown, the reservation stays at max
    u = r.usage
    tokens_in = (
        u.input_tokens + (u.cache_creation_input_tokens or 0) + (u.cache_read_input_tokens or 0)
    )  # ponytail: cache priced as plain input
    usd = (tokens_in * m["price_in"] + u.output_tokens * m["price_out"]) / 1e6
    costs.settle(team.home, rid, tokens_in, u.output_tokens, usd)
    if r.stop_reason != "end_turn":
        raise RuntimeError(f"{role} stopped with {r.stop_reason}; no usable answer")
    return json.loads(next(b.text for b in r.content if b.type == "text"))


BRAIN_SYSTEM = """You are the brain (tech lead) of Qarau's dev team. The owner does not review
code: automated gates (local checks, an LLM tester, GitHub CI, benchmark, secret scan) decide
whether work merges. So your plan and acceptance criteria are the spec the gates enforce.

For each task you pick, write:
- plan: concrete steps for a senior engineer (Claude Code) working alone in a git worktree:
  which modules to touch, data formats, tests to add, how to verify on real data. Name files.
- acceptance_criteria: 3-7 short, objectively checkable statements the tester can verify from
  the diff and check output (no "works well"). Include the tests that must exist.
- licence_notes: every model or library the plan may use and its licence; it must allow
  commercial use (VGGT weights are CC-BY-NC: never). If unclear, say so and avoid it.
Developers cannot commit, push or create branches: the orchestrator commits after each round,
so every developer run happens on uncommitted code (anything tagged with a commit hash shows
"<hash>+dirty"). Never write a criterion that needs the developer to commit or to produce a
clean commit hash; real per-commit numbers (e.g. the benchmark) are produced by the merge gate
on the committed code. Long jobs (full benchmark) must fit in one developer round (~60 min).
Pick at most one task per developer, in backlog order, only for the developers listed.
Respect the repository rules (CLAUDE.md). Keep each task small enough for one PR."""

TESTER_SYSTEM = """You are the tester of Qarau's dev team. There is no human reviewer: your verdict
plus the automated checks decide whether this change merges into `dev`. Be strict and fair.
Judge each acceptance criterion from the diff, the developer's summary and the check output.
A criterion is met only if the diff shows it (code + tests), not just because the developer
says so. Missing tests, placeholders, TODO stubs, silently skipped work, or edits unrelated to
the task fail. Check results are given; failing checks are handled separately, but mention
them in feedback if relevant.
The developer cannot commit (the orchestrator commits after each round), so results of the
developer's own runs tagged "<hash>+dirty" or with an earlier commit hash are expected: never
fail a criterion for that. Judge whether the mechanism records the commit correctly (code and
tests), not whether the developer's run was made on a clean commit.
Write `feedback` for the developer in English: concrete, actionable, empty if passed.
Write `summary` and `how_to_try` for the owner in simple Russian: what changed in plain
words (no jargon), and exact commands to try it."""


class RealAgents:
    """v1: brain (API), dev_pipeline (Claude Code headless, subscription), tester (API)."""

    TOOLS = {"claude"}  # developer tools implemented so far; dev_app (codex) comes in v2

    # --- brain ---------------------------------------------------------------------------
    def plan(self, team, todo, feedback=None):
        devs = {n: d for n, d in team.cfg["developers"].items() if d["tool"] in self.TOOLS}
        root = team.root
        read = lambda p: (root / p).read_text() if (root / p).exists() else "(missing)"  # noqa: E731
        files = team.git("ls-files", "pipeline", "backend", "infra", "web/src").split("\n")
        samples = (
            sorted(p.name for p in (root / "data" / "samples").glob("*"))
            if (root / "data" / "samples").exists()
            else []
        )
        prompt = "\n\n".join(
            [
                "<claude_md>\n" + read("CLAUDE.md") + "\n</claude_md>",
                "<phase1_plan>\n" + read("docs/plan/phase1.md") + "\n</phase1_plan>",
                "<benchmark_notes>\n" + read("docs/notes/benchmark.md") + "\n</benchmark_notes>",
                "<repository_files>\n" + "\n".join(f for f in files if f) + "\n</repository_files>",
                "<data_samples dir='data/samples' note='read-only, not in git'>\n"
                + "\n".join(samples)
                + "\n</data_samples>",
                "<developers>\n"
                + yaml.safe_dump(
                    {n: {"areas": d["areas"], "owns": d["owns"]} for n, d in devs.items()}
                )
                + "</developers>",
                "<open_tasks>\n"
                + yaml.safe_dump(todo, allow_unicode=True, sort_keys=False)
                + "</open_tasks>",
            ]
            + (
                [f"<previous_plan_rejected>\n{feedback}\n</previous_plan_rejected>"]
                if feedback
                else []
            )
        )
        schema = {
            "type": "object",
            "properties": {
                "assignments": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "task_id": {"type": "string"},
                            "assignee": {"type": "string", "enum": sorted(devs)},
                            "plan": {"type": "string"},
                            "acceptance_criteria": {"type": "array", "items": {"type": "string"}},
                            "licence_notes": {"type": "string"},
                        },
                        "required": [
                            "task_id",
                            "assignee",
                            "plan",
                            "acceptance_criteria",
                            "licence_notes",
                        ],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["assignments"],
            "additionalProperties": False,
        }
        out = llm_call(
            team,
            "brain",
            ",".join(t["id"] for t in todo),
            BRAIN_SYSTEM,
            prompt,
            schema,
            max_tokens=32000,
        )
        return [
            {
                "task_id": a["task_id"],
                "assignee": a["assignee"],
                "plan": a["plan"] + "\n\nLicences: " + a["licence_notes"],
                "criteria": a["acceptance_criteria"],
            }
            for a in out["assignments"]
        ]

    # --- developers ----------------------------------------------------------------------
    def develop(self, team, s):
        dev = team.cfg["developers"][s["assignee"]]
        if dev["tool"] != "claude":
            raise NotImplementedError(f"{dev['tool']} developer comes in v2")
        return self._claude(team, s, dev)

    def _brief(self, team, s, dev) -> str:
        t = s["task"]
        crit = "\n".join(f"- {c}" for c in s["criteria"])
        checks = "\n".join(f"- `{c}`" for c in dev.get("checks", []))
        return f"""You are {s["assignee"]} on Qarau's dev team, working alone in this git worktree.
No human reviews your work: an LLM tester, the checks below, GitHub CI, a benchmark and a
secret scan decide whether it merges. Read CLAUDE.md first and follow it.

Task {t["id"]}: {t["title"]}
Goal: {t.get("goal", "").strip()}

Plan from the tech lead:
{s["plan"]}

Acceptance criteria (all must be visibly met in your diff, with tests):
{crit}

Rules:
- Edit only {", ".join(dev["owns"])} and docs/. Anything else is reverted automatically.
  Do not edit docs/notes/progress.md: parallel tasks would conflict there (the daily
  report covers progress).
- Do not commit, push, merge or create branches; the orchestrator commits your changes.
- Never write keys or tokens into files. Never write under data/. Sample videos (read-only):
  {team.root / "data" / "samples"}
- Before using any model or library, check its licence allows commercial use; note it.
- Your runs are on uncommitted code, so anything tagged with a commit hash shows "+dirty";
  that is expected. If a request needs a commit, say so instead of trying to commit.
- Run these checks and make them pass before you finish:
{checks}
- Finish with a short summary: what you changed, how you verified it, what is left."""

    def _claude(self, team, s, dev) -> dict:
        env = {
            k: v
            for k, v in os.environ.items()
            if k
            not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "CLAUDECODE")
            and not k.startswith("CLAUDE_CODE_")
        }
        if not team.secrets.get("CLAUDE_CODE_OAUTH_TOKEN"):
            raise RuntimeError("CLAUDE_CODE_OAUTH_TOKEN is missing in devteam/.env")
        env["CLAUDE_CODE_OAUTH_TOKEN"] = team.secrets["CLAUDE_CODE_OAUTH_TOKEN"]
        cmd = [
            "claude",
            "-p",
            "--output-format",
            "json",
            "--permission-mode",
            "acceptEdits",
            "--allowedTools",
            ",".join(dev["allowed_tools"]),
            "--disallowedTools",
            ",".join(dev.get("disallowed_tools", [])),
            "--add-dir",
            str(team.root / "data" / "samples"),
        ]
        if dev.get("model"):
            cmd += ["--model", dev["model"]]
        if s.get("session") and s.get("feedback"):
            cmd += ["--resume", s["session"]]
            prompt = (
                f"The tester or the merge gates sent your work back:\n\n{s['feedback']}"
                "\n\nFix it, run the checks again, and finish with a short summary."
            )
        else:
            prompt = self._brief(team, s, dev)
        r = team.run_proc(
            cmd, cwd=s["worktree"], timeout=dev["timeout_min"] * 60, input=prompt, env=env
        )
        try:
            out = json.loads(r["stdout"])
        except json.JSONDecodeError:
            raise RuntimeError(
                f"claude gave no JSON (exit {r['code']}): "
                f"{team.redact(r['stderr'][-800:] or r['stdout'][-800:])}"
            ) from None
        u = out.get("usage") or {}
        costs.record(
            team.home,
            s["assignee"],
            dev.get("model") or "claude-code (subscription)",
            s["task"]["id"],
            u.get("input_tokens", 0)
            + u.get("cache_read_input_tokens", 0)
            + u.get("cache_creation_input_tokens", 0),
            u.get("output_tokens", 0),
            out.get("total_cost_usd", 0.0),
            billed=False,
        )
        if out.get("is_error"):  # usage limit, auth, crash: stop; `devteam resume` retries
            raise RuntimeError(f"claude failed: {team.redact(str(out.get('result'))[:800])}")
        return {"session": out["session_id"], "summary": out.get("result", "")}

    # --- tester --------------------------------------------------------------------------
    def judge(self, team, s, checks):
        base = f"{team.remote_name}/{team.cfg['base_branch']}"
        diff = team.git("diff", f"{base}...HEAD", cwd=s["worktree"])
        limit = 120_000
        note = ""
        if len(diff) > limit:  # said out loud, never silent
            note = f"\n[diff truncated: showing {limit} of {len(diff)} characters]"
            diff = diff[:limit]
        stat = team.git("diff", "--stat", f"{base}...HEAD", cwd=s["worktree"])
        check_text = (
            "\n\n".join(
                f"$ {c['cmd']}  -> {'ok' if c['ok'] else 'FAILED'}\n{c['output'][-2000:]}"
                for c in checks
            )
            or "(no checks)"
        )
        crit = "\n".join(f"- {c}" for c in s["criteria"])
        prompt = (
            f"<task>\n{s['task']['id']}: {s['task']['title']}\n"
            f"{s['task'].get('goal', '')}\n</task>\n\n<plan>\n{s['plan']}\n</plan>\n\n"
            f"<acceptance_criteria>\n{crit}\n</acceptance_criteria>\n\n"
            f"<developer_summary>\n{s.get('dev_summary', '')}\n</developer_summary>\n\n"
            f"<checks>\n{check_text}\n</checks>\n\n<diffstat>\n{stat}</diffstat>\n\n"
            f"<diff>\n{diff}{note}\n</diff>"
        )
        schema = {
            "type": "object",
            "properties": {
                "passed": {"type": "boolean"},
                "criteria": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "criterion": {"type": "string"},
                            "met": {"type": "boolean"},
                            "note": {"type": "string"},
                        },
                        "required": ["criterion", "met", "note"],
                        "additionalProperties": False,
                    },
                },
                "feedback": {"type": "string"},
                "summary": {"type": "string"},
                "how_to_try": {"type": "string"},
            },
            "required": ["passed", "criteria", "feedback", "summary", "how_to_try"],
            "additionalProperties": False,
        }
        v = llm_call(team, "tester", s["task"]["id"], TESTER_SYSTEM, team.redact(prompt), schema)
        v["passed"] = v["passed"] and all(c["met"] for c in v["criteria"])
        return v
