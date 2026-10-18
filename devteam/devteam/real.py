"""Real agents. Every pay-per-use model call goes through llm_call (budget + cost log)."""

import base64
import json
import os
import time
from pathlib import Path

import anthropic
import httpx
import yaml

from devteam import costs
from devteam import gates as G
from devteam import supervise as SV
from devteam.supervise import TaskHalted

RETRY_WAITS = (15, 60, 180)  # seconds before each retry of a transient API failure
# Prompt cache: brain calls come minutes to an hour apart, tester rounds of one task too, so the
# 1-hour TTL (writes cost 2x input, reads ~0.05-0.1x: config `price_cache_read`).
CACHE = {"type": "ephemeral", "ttl": "1h"}
CACHE_WRITE_X = 2.0


def transient(e: Exception) -> bool:
    """Worth retrying: network drops, timeouts, rate limits, server errors, overload."""
    if isinstance(e, (anthropic.APIConnectionError, httpx.TransportError)):
        return True
    if not isinstance(e, anthropic.APIStatusError):
        return False
    err = e.body.get("error") if isinstance(e.body, dict) else None
    kind = err.get("type") if isinstance(err, dict) else None
    return (
        e.status_code in (408, 409, 429)
        or e.status_code >= 500
        or kind
        in (
            "overloaded_error",
            "api_error",
            "rate_limit_error",
        )
    )  # a mid-stream error event arrives with HTTP 200, the kind is in the body


def llm_call(
    team,
    role: str,
    task: str,
    system: str,
    prompt: str,
    schema: dict,
    max_tokens: int = 16000,
    client=None,
    images: list[str] = (),
    context: str = "",
) -> dict:
    """One structured-output call for `role` (a key of config.models). Reserves the
    worst-case cost first, settles to the real usage after. No SDK retries: transient failures
    (connection, timeout, 429, 5xx, overloaded) are retried here after RETRY_WAITS, each try
    with its own reservation; then the error is raised (`devteam resume` re-runs the node).
    Prompt caching: `system` and `context` (the stable part of the user turn: repository
    rules, the task and its criteria) are cached; images and `prompt` come after them."""
    m = team.cfg["models"][role]
    if m["provider"] != "anthropic":
        raise NotImplementedError(f"provider {m['provider']!r} for {role}: only anthropic so far")
    # Every token is at least one byte, so UTF-8 bytes bound the input tokens from above.
    est_in = len((system + context + prompt + json.dumps(schema)).encode())
    est_in += 1000 + 2500 * len(images)
    # worst case: every input token is a cache write
    max_usd = (est_in * m["price_in"] * CACHE_WRITE_X + max_tokens * m["price_out"]) / 1e6
    if client is None:
        if not team.secrets.get("ANTHROPIC_API_KEY"):
            raise RuntimeError("ANTHROPIC_API_KEY is missing in devteam/.env")
        client = anthropic.Anthropic(api_key=team.secrets["ANTHROPIC_API_KEY"])
    if hasattr(client, "with_options"):
        client = client.with_options(max_retries=0)  # one reservation = one request
    content: list = [{"type": "text", "text": context, "cache_control": CACHE}] if context else []
    content += [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": base64.b64encode(Path(i).read_bytes()).decode(),
            },
        }
        for i in images
    ]
    content.append({"type": "text", "text": prompt})
    for wait in (*RETRY_WAITS, None):
        rid = costs.reserve(
            team.home, team.cfg["daily_budget_usd"], role, m["model"], task, max_usd
        )
        try:
            with client.messages.stream(  # streaming: long thinking can't hit HTTP timeouts
                model=m["model"],
                max_tokens=max_tokens,
                system=[{"type": "text", "text": system, "cache_control": CACHE}],
                messages=[{"role": "user", "content": content}],
                output_config={
                    "effort": m["effort"],
                    "format": {"type": "json_schema", "schema": schema},
                },
            ) as stream:
                r = stream.get_final_message()
            break
        except (anthropic.APIError, httpx.TransportError) as e:
            if isinstance(e, anthropic.APIStatusError) and 400 <= e.status_code < 500:
                costs.release(team.home, rid)  # rejected before any generation: nothing billed
            # else 5xx / connection / timeout / mid-stream error: billing unknown, stays at max
            if wait is None or not transient(e):
                raise
            team.emit(role, f"API call failed ({e}); retrying in {wait}s", task=task)
            time.sleep(wait)
    u = r.usage
    read, wrote = u.cache_read_input_tokens or 0, u.cache_creation_input_tokens or 0
    usd = (
        u.input_tokens * m["price_in"]
        + read * m.get("price_cache_read", m["price_in"] * 0.1)
        + wrote * m["price_in"] * CACHE_WRITE_X
        + u.output_tokens * m["price_out"]
    ) / 1e6
    tokens_in = u.input_tokens + read + wrote
    costs.settle(team.home, rid, tokens_in, u.output_tokens, usd, read, wrote)
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
on the committed code. Developers run only the QUICK benchmark (named in the prompt) for
their own numbers; the full benchmark runs only at the merge gate and at release, so never
ask a developer to run the full set.
List `routes` for every task whose acceptance mentions a visual result in the viewer, pipeline
tasks too: the app pages (e.g. "/", "/viewer/demo") the tester screenshots and looks at; []
only for tasks with nothing to see. For pipeline tasks the viewer serves the developer's quick
benchmark output, so routes are "/viewer/<video>" for videos in that run. Developers cannot
take screenshots (Codex runs in a sandbox with no network and no browser): never require a
developer to produce, attach or commit a screenshot. Write visual criteria as what the page
must show ("/viewer/demo shows a black canvas with points and no error banner"); the tester
judges them from its own screenshots of `routes`, and a missing screenshot fails the task.
A task with `previous_work` already has commits on its devteam/<id> branch: the developer
continues on that branch (dev merged in), so plan only what is still missing; say in the plan
that the earlier work is there. Tasks whose `depends_on` are not all done never reach you.
Never require edits to docs/notes/progress.md: developers may not touch it (parallel
tasks would conflict there); the daily report covers progress.
Never plan a step that spends money: no paid external services (RunPod, cloud GPUs, paid APIs);
a task that needs one is the owner's call, so leave it out.
A developer edits only its own folders (`owns` in <developers>) and docs/; anything else is
reverted. Never plan a task whose acceptance needs files outside the assignee's folders (A7 asked
the web developer for backend/ routes and went round in circles). Do not plan such a task:
list it in `needs_split` with the reason and the proposed parts, one per developer, and the
owner splits it. Name a folder in a criterion only if the developer must change it there.
A task with `writable` may also add new files in those data folders (never change existing ones).
Pick at most one task per developer, in backlog order, only for the developers listed.
Respect the repository rules (CLAUDE.md). Keep each task small enough for one PR."""

TESTER_SYSTEM = """You are the tester of Qarau's dev team. There is no human reviewer: your verdict
plus the automated checks decide whether this change merges into `dev`. Be strict and fair.
Judge each acceptance criterion from the diff, the developer's summary and the check output.
A criterion is met only if the diff shows it (code + tests), not just because the developer
says so. Missing tests, placeholders, TODO stubs, silently skipped work, or edits unrelated to
the task fail. Check results are given; failing checks are handled separately, but mention
them in feedback if relevant. Screenshots of the app pages, when attached, are the real UI and
were taken by the orchestrator for you: judge visible criteria (layout, text via i18n, errors
on screen) from them. Developers cannot take screenshots (sandbox without network or browser):
a criterion that asks for a screenshot or visual proof is met or failed by what YOUR
screenshots show, never failed because the developer produced none. A visual criterion in the
viewer (web or pipeline task) with no screenshot of yours (none attached, or listed in
<screenshots_failed>) is NOT met: never pass it from the code alone.
Developers run only a quick benchmark subset; the full benchmark is a merge gate after you:
never fail a criterion because the developer did not run the full set.
Screenshots named `dev-*.png` show the same page on current `dev` (same viewer code, dev's
benchmark output) for criteria that compare the branch with dev.
Sparse point clouds never look like the object: do not judge whether a building or object is
recognisable before dense reconstruction (task P4). Judge only what the criterion names (e.g. no
wall or band of points in the sky, far points only in the far layer).
The developer cannot commit (the orchestrator commits after each round), so results of the
developer's own runs tagged "<hash>+dirty" or with an earlier commit hash are expected: never
fail a criterion for that. Judge whether the mechanism records the commit correctly (code and
tests), not whether the developer's run was made on a clean commit.
Write `feedback` for the developer in English: concrete, actionable, empty if passed.
Write `summary` and `how_to_try` for the owner in simple Russian: what changed in plain
words (no jargon), and exact commands to try it."""


class RealAgents:
    """v1: brain (API), dev_pipeline (Claude Code headless, subscription), tester (API)."""

    TOOLS = {"claude", "codex"}  # developer tools implemented

    # --- brain ---------------------------------------------------------------------------
    def plan(self, team, todo, feedback=None, devs=None):
        devs = {
            n: d
            for n, d in team.cfg["developers"].items()
            if d["tool"] in self.TOOLS and (devs is None or n in devs)
        }
        root = team.root
        read = lambda p: (root / p).read_text() if (root / p).exists() else "(missing)"  # noqa: E731
        files = team.git("ls-files", "pipeline", "backend", "infra", "web/src").split("\n")
        samples = (
            sorted(p.name for p in (root / "data" / "samples").glob("*"))
            if (root / "data" / "samples").exists()
            else []
        )
        context = "\n\n".join(  # stable between calls: cached
            [
                "<claude_md>\n" + read("CLAUDE.md") + "\n</claude_md>",
                "<phase1_plan>\n" + read("docs/plan/phase1.md") + "\n</phase1_plan>",
                "<benchmark_notes>\n" + read("docs/notes/benchmark.md") + "\n</benchmark_notes>",
                "<repository_files>\n" + "\n".join(f for f in files if f) + "\n</repository_files>",
                "<data_samples dir='data/samples' note='read-only, not in git'>\n"
                + "\n".join(samples)
                + "\n</data_samples>",
                f"<benchmark_policy>\n{quick_policy(team)}\n</benchmark_policy>",
            ]
        )
        prompt = "\n\n".join(
            [
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
                            "routes": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": [
                            "task_id",
                            "assignee",
                            "plan",
                            "acceptance_criteria",
                            "licence_notes",
                            "routes",
                        ],
                        "additionalProperties": False,
                    },
                },
                "needs_split": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "task_id": {"type": "string"},
                            "why": {"type": "string"},
                            "parts": {"type": "string"},
                        },
                        "required": ["task_id", "why", "parts"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["assignments", "needs_split"],
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
            context=context,
        )
        splits = [
            {"task_id": x["task_id"], "split": f"{x['why']} Proposed parts: {x['parts']}"}
            for x in out.get("needs_split") or []
        ]
        return splits + [
            {
                "task_id": a["task_id"],
                "assignee": a["assignee"],
                "plan": a["plan"] + "\n\nLicences: " + a["licence_notes"],
                "criteria": a["acceptance_criteria"],
                "routes": a["routes"],
            }
            for a in out["assignments"]
        ]

    # --- developers ----------------------------------------------------------------------
    def develop(self, team, s):
        dev = team.cfg["developers"][s["assignee"]]
        t0, halted = time.monotonic(), None
        try:
            run = self._codex if dev["tool"] == "codex" else self._claude
            out = run(team, s, dev)
        except TaskHalted as e:  # killed by the supervisor or the owner: its tokens still count
            halted, out = e, {"usage": getattr(e, "usage", {"in": 0, "out": 0, "usd": 0.0})}
        u = out.pop("usage")
        costs.record(
            team.home,
            s["assignee"],
            dev.get("model")
            or {"claude": "claude-code"}.get(dev["tool"], dev["tool"]) + " (subscription)",
            s["task"]["id"],
            u["in"],
            u["out"],
            u["usd"],
            billed=False,
            secs=time.monotonic() - t0,
        )
        if halted:
            raise halted
        out["tokens"] = u["in"] + u["out"]
        if out.pop("error", None):  # usage limit, auth, crash: stop; `devteam resume` retries
            msg = f"{dev['tool']} failed: {team.redact(out['summary'][:800])}"
            hit, reset_at = costs.limit_reset(out["summary"])
            if hit:  # continuous mode waits for the reset, a single run stops
                raise costs.SubscriptionLimit(msg, reset_at)
            raise RuntimeError(msg)
        return out

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
- Never write keys or tokens into files. Never write under data/{writable_rule(team, t)}. Sample
  videos (read-only): {team.root / "data" / "samples"}
- Scratch output (experiments, temp scripts, logs, renders) goes ONLY to
  {team.home / "scratch" / t["id"]}, never inside this worktree (no scratch/ or tmp files
  here): files outside your folders are reverted, and they bloat the review.
- Before using any model or library, check its licence allows commercial use; note it.
- Your runs are on uncommitted code, so anything tagged with a commit hash shows "+dirty";
  that is expected. If a request needs a commit, say so instead of trying to commit.
- Do not try to take screenshots: the tester screenshots the app pages itself and judges
  visual criteria from them.
- Stop every server you start (dev server, preview, test server) as soon as its check is
  done; never leave a listening port. When your run ends, the orchestrator kills anything
  you left and reports each port it had to close.
{quick_benchmark(team, s, dev)}- Run these checks and make them pass before you finish:
{checks}
- Finish with a short summary: what you changed, how you verified it, what is left."""

    def _claude(self, team, s, dev) -> dict:
        env = {
            k: v
            for k, v in os.environ.items()
            if k
            not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "CLAUDECODE")
            and not k.startswith("CLAUDE_CODE_")
            and not paid_service_key(k)
        }
        if not team.secrets.get("CLAUDE_CODE_OAUTH_TOKEN"):
            raise RuntimeError("CLAUDE_CODE_OAUTH_TOKEN is missing in devteam/.env")
        env["CLAUDE_CODE_OAUTH_TOKEN"] = team.secrets["CLAUDE_CODE_OAUTH_TOKEN"]
        cmd = [
            "claude",
            "-p",
            "--output-format",
            "stream-json",  # usage per API message: the supervisor counts tokens live
            "--verbose",
            "--permission-mode",
            "acceptEdits",
            "--allowedTools",
            ",".join(dev["allowed_tools"]),
            "--disallowedTools",
            ",".join(dev.get("disallowed_tools", [])),
            "--add-dir",
            str(team.root / "data" / "samples"),
            "--add-dir",
            str(team.home / "scratch" / s["task"]["id"]),
        ]
        for d in s["task"].get("writable") or []:
            cmd += ["--add-dir", str(team.root / d)]
        if dev.get("model"):
            cmd += ["--model", dev["model"]]
        prompt = self._prompt(team, s, dev)
        if s.get("session") and (s.get("feedback") or s.get("conflicts")):
            cmd += ["--resume", s["session"]]
        r = self._run(team, s, dev, cmd, prompt, env, SV.ClaudeTokens())
        results = [
            e
            for e in (_json(line) for line in r["stdout"].splitlines())
            if e.get("type") == "result"
        ]
        if results:
            out = results[-1]
        else:
            if costs.limit_reset(r["stderr"] + r["stdout"])[0]:
                raise costs.SubscriptionLimit(
                    f"claude: {team.redact((r['stderr'] or r['stdout'])[-800:])}",
                    costs.limit_reset(r["stderr"] + r["stdout"])[1],
                )
            raise RuntimeError(
                f"claude gave no result (exit {r['code']}): "
                f"{team.redact(r['stderr'][-800:] or r['stdout'][-800:])}"
            )
        u = out.get("usage") or {}
        return {
            "session": out.get("session_id") or s.get("session", ""),
            "summary": str(out.get("result", "")),
            "error": bool(out.get("is_error")),
            "usage": {
                "in": u.get("input_tokens", 0)
                + u.get("cache_read_input_tokens", 0)
                + u.get("cache_creation_input_tokens", 0),
                "out": u.get("output_tokens", 0),
                "usd": out.get("total_cost_usd", 0.0),  # notional: what the API would charge
            },
        }

    def _codex(self, team, s, dev) -> dict:
        """Codex CLI on the saved ChatGPT login (never an API key). Sandbox: writes only in
        the worktree, no network, no .git writes; the orchestrator installs and commits."""
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in ("OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_BASE_URL")
            and not paid_service_key(k)
        }
        cmd = ["codex", "exec", "--json", "--sandbox", "workspace-write", "-C", s["worktree"]]
        cmd += ["--add-dir", str(team.home / "scratch" / s["task"]["id"])]  # writable too
        for d in s["task"].get("writable") or []:
            cmd += ["--add-dir", str(team.root / d)]
        if dev.get("model"):
            cmd += ["-m", dev["model"]]
        prompt, resume = self._prompt(team, s, dev), ""
        if s.get("session") and (s.get("feedback") or s.get("conflicts")):
            resume = s["session"]
            cmd += ["resume", resume, "-"]
        else:
            cmd += ["-"]
        r = self._run(team, s, dev, cmd, prompt, env, SV.CodexTokens(resume))
        return parse_codex(r["stdout"], r["ok"], r["stderr"], s.get("session", ""))

    def _prompt(self, team, s, dev) -> str:
        if s.get("conflicts"):
            files = "\n".join(f"- {f}" for f in s["conflicts"])
            return (
                f"The orchestrator merged the latest `{team.cfg['base_branch']}` into this task "
                f"branch and git reports conflicts in:\n{files}\n\nThis is a short step: resolve "
                "the conflict markers (<<<<<<< ======= >>>>>>>) in exactly these files, keeping "
                "the intent of both sides (the other team's changes and this task's work). Change "
                "nothing else (other edits are reverted). Do not commit, merge or abort: the "
                "orchestrator concludes the merge. Finish with a one-line summary."
            )
        if s.get("session") and s.get("feedback"):
            return (
                f"The tester or the merge gates sent your work back:\n\n{s['feedback']}"
                "\n\nFix it, run the checks again, and finish with a short summary."
            )
        return self._brief(team, s, dev)

    def _run(self, team, s, dev, cmd, prompt, env, counter) -> dict:
        """The developer's process, watched: tokens live, minutes, `devteam stop <task>`."""
        watch = SV.RoundWatch(counter, s.get("tokens", 0), SV.limits(team.cfg))
        try:
            r = team.run_proc(
                cmd,
                cwd=s["worktree"],
                timeout=dev["timeout_min"] * 60,
                input=prompt,
                env=env,
                agent=s["assignee"],
                watch=watch,
            )
        except TaskHalted as e:
            e.usage = {"in": watch.tokens, "out": 0, "usd": 0.0}  # ponytail: in + out together
            raise
        if r["timed_out"]:
            e = TaskHalted(f"supervisor: the round hit timeout_min ({dev['timeout_min']} min)")
            e.usage = {"in": watch.tokens, "out": 0, "usd": 0.0}
            raise e
        return r

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
        context = (  # the same in every round of this task: cached
            f"<task>\n{s['task']['id']}: {s['task']['title']}\n"
            f"{s['task'].get('goal', '')}\n</task>\n\n<plan>\n{s['plan']}\n</plan>\n\n"
            f"<acceptance_criteria>\n{crit}\n</acceptance_criteria>"
        )
        prompt = (
            f"<developer_summary>\n{s.get('dev_summary', '')}\n</developer_summary>\n\n"
            f"<checks>\n{check_text}\n</checks>\n\n"
            + (
                "<screenshots_failed>\n"
                + "\n".join(s["screenshot_problems"])
                + "\n</screenshots_failed>\n\n"
                if s.get("screenshot_problems")
                else ""
            )
            + f"<diffstat>\n{stat}</diffstat>\n\n"
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
        shots = s.get("screenshots") or []
        if shots:
            prompt = (
                "Screenshots above (after the task), in order: "
                + ", ".join(Path(p).name for p in shots)
                + "\n\n"
                + prompt
            )
        v = llm_call(
            team,
            "tester",
            s["task"]["id"],
            TESTER_SYSTEM,
            team.redact(prompt),
            schema,
            images=shots,
            context=team.redact(context),
        )
        v["passed"] = v["passed"] and all(c["met"] for c in v["criteria"])
        return v


def _json(line: str) -> dict:
    try:
        e = json.loads(line)
    except ValueError:
        return {}
    return e if isinstance(e, dict) else {}


def writable_rule(team, task: dict) -> str:
    if not task.get("writable"):
        return ""
    dirs = ", ".join(str(team.root / d) for d in task["writable"])
    return (
        f", except that this task may ADD new files and folders under {dirs} (never change, "
        "move or delete an existing one there: the orchestrator checks and blocks the task)"
    )


def paid_service_key(name: str) -> bool:
    """Credentials for services that bill per use never reach a developer agent."""
    return any(p in name.upper() for p in ("RUNPOD", "AWS_", "GOOGLE_APPLICATION", "VAST_"))


def parse_codex(stdout: str, ok: bool, stderr: str, session: str) -> dict:
    """`codex exec --json` JSONL: thread.started -> items -> turn.completed (usage) or
    turn.failed / error. The summary is the last agent message."""
    summary, err, usage = "", "", {"in": 0, "out": 0, "usd": 0.0}
    for line in stdout.splitlines():
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = e.get("type")
        if kind == "thread.started":
            session = e["thread_id"]
        elif kind == "item.completed" and e["item"].get("type") == "agent_message":
            summary = e["item"]["text"]
        elif kind == "turn.completed":
            u = e.get("usage") or {}
            usage["in"] += u.get("input_tokens", 0)  # includes cached_input_tokens
            usage["out"] += u.get("output_tokens", 0)
        elif kind == "turn.failed":
            err = (e.get("error") or {}).get("message", "turn failed")
        elif kind == "error":
            err = e.get("message", "error")
    if not ok and not err:
        err = stderr[-800:] or "codex exited with an error and no message"
    return {
        "session": session,
        "summary": err or summary,
        "error": bool(err) or not session,
        "usage": usage,
    }


def quick_policy(team) -> str:
    b = team.cfg["gates"]["benchmark"]
    return (
        f"Developers measure with the quick benchmark only: `{b['command']} --only "
        f"{','.join(b['quick_only'])}` (~{b['quick_min']} min). The full set (all videos, "
        f"~{b['timeout_min']} min) runs only at the merge gate and at release."
    )


def quick_benchmark(team, s, dev) -> str:
    """Brief lines for developers whose folders the benchmark gate watches."""
    b = team.cfg["gates"]["benchmark"]
    if not any(p in b["when_paths"] for p in dev["owns"]):
        return ""
    quick = s["task"].get("quick_only") or b["quick_only"]
    out = team.home / "bench-dev" / s["task"]["id"]
    cmd = b["command"].format(data=team.root / "data" / "samples", out=out)
    base = None
    try:
        base = G.cached_benchmark(team, f"{team.remote_name}/{team.cfg['base_branch']}")
    except RuntimeError:  # no remote-tracking ref yet
        pass
    rows = {k: v for k, v in (base or {}).get("videos", {}).items() if k in quick}
    baseline = (
        "Baseline on dev for these videos (full gate run, no need to re-measure it):\n"
        + G.bench_table({"videos": rows})
        if rows
        else "No cached dev baseline: measure before and after your change."
    )
    return (
        f"- Benchmark: run ONLY the quick set (~{b['quick_min']} min):\n"
        f"  `{cmd} --only {','.join(quick)}`\n"
        "  Never run the full set (all videos, ~70 min): the merge gate runs it on the\n"
        "  committed code and blocks regressions.\n"
        + "\n".join("  " + line for line in baseline.splitlines())
        + "\n"
    )
