"""The team as a LangGraph graph. No human review: automated gates decide.

brain (plan, validated) -> claim (backlog update) -> one subgraph per task, in parallel:
    worktree -> develop -> test -> gates -> finish
test or gates fail -> back to develop (max N fixes) -> then "blocked".
gates = merge latest dev in, secret scan, benchmark, push, PR into dev, GitHub CI, auto-merge.
"""

import contextlib
import fnmatch
import hashlib
import json
import operator
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import traceback
from pathlib import Path
from typing import Annotated, Any, TypedDict

import yaml
from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from devteam import gates as G
from devteam import procs, watchdog
from devteam import scope as SC
from devteam import supervise as SV
from devteam.costs import BudgetExceeded, SubscriptionLimit
from devteam.supervise import TaskHalted

AGENTS = ["brain", "architect", "analyst", "dev_pipeline", "dev_app", "tester"]
GIT_NET_COMMANDS = {"fetch", "push", "ls-remote", "pull"}
GIT_NET_WAITS = (10, 30, 90)  # seconds before each retry of a git network command
GIT_NET_ERROR = re.compile(
    r"Could not resolve host|Failed to connect|Connection (refused|reset|timed out)|"
    r"Operation timed out|Network is unreachable|"
    r"The requested URL returned error: 5\d\d|early EOF|unexpected disconnect",
    re.I,
)
# Never touched by a developer, tracked or ignored. Checked by snapshot (git can't see ignored).
PROTECTED = [".env", ".env.*", "*/.env", "*/.env.*", "data/*", "scratch/*", "*.sqlite"]
PROTECTED_OK = [".env.example", "*/.env.example"]
SNAPSHOT_SKIP = {
    ".git",
    "node_modules",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    "dist",
    ".vite",
}
# git commands the orchestrator may never run (history rewrite, deletion)
# Task status lives in backlog.yaml; these fields must match origin/dev before a run.
STATUS_FIELDS = ("status", "owner", "run", "branch", "pr", "done_at")
FORBIDDEN_GIT = {"rebase", "filter-branch", "filter-repo", "replace", "update-ref"}
FORBIDDEN_PUSH = {
    "--force",
    "-f",
    "--force-with-lease",
    "--force-if-includes",
    "--delete",
    "-d",
    "--mirror",
    "--all",
    "--tags",
    "--prune",
}
RELEASE_TAG = re.compile(r"refs/tags/release-\d{4}-\d{2}-\d{2}(\.\d+)?")  # pushed, never moved
FORBIDDEN_BRANCH = {"-D", "-d", "--delete", "-M", "-m", "--move", "-f", "--force"}
POLL_S = 5  # a running step is checked this often (limits, `devteam stop <task>`)
CPU_EVERY_S = 60  # supervisor: how often a round's CPU use is sampled
SLEPT_S = 60  # wall clock ahead of the monotonic one by more: the machine slept
# Task size (owner, 2026-10-27: DS1 got 16 criteria in 2 parts for a backlog entry with 5)
MAX_PARTS = 3  # more: the task goes to needs_split
MAX_PART_CRITERIA = 8
FIRST_PART_CRITERIA = 5  # part 1 of several stays small
MAX_ADDED_CRITERIA = 3  # over the backlog entry's own, each explained in the plan
OWNER_STOP = "stopped by the owner"
PLAN_CHANGE = ("backlog: analyst:", "backlog: architect:")  # the commits `devteam undo` reverts
DEPS_WAIT = "waits for"  # blocked_reason of a task parked on its depends_on, e.g. "waits for A7a"
CONFLICT = re.compile(rb"^(<{7}|>{7})( |$)", re.M)  # markers git leaves (not "=======": markdown)


def waits_on_dependencies(t: dict) -> bool:
    """Blocked only until its depends_on merge: blocked_reason "waits for <dependency>". Blocked by
    fix loops, gates, limits, a crash or the owner: only `devteam resume` moves it (owner,
    2026-10-27: RPT1, blocked by the tester, went back to todo when another task merged)."""
    reason = (t.get("blocked_reason") or "").strip()
    first = re.split(r"[\s,;:.()]+", reason.removeprefix(DEPS_WAIT + " "), maxsplit=1)[0]
    return reason.startswith(DEPS_WAIT + " ") and first in (t.get("depends_on") or [])


def _feed(pipe, text: str):
    """stdin for a step, from a thread: a large brief must not block the wait."""
    try:
        pipe.write(text.encode("utf-8", "replace"))
        pipe.close()
    except (BrokenPipeError, OSError):
        pass


class Violation(RuntimeError):
    """The worktree or branch is not what the graph expects: stop, never commit."""


class StopRun(Exception):
    """A clean stop was asked (Ctrl+C in continuous mode, a run limit): raised at the start of
    a node, so the steps before it stay checkpointed and nothing is half done."""


def is_protected(path: str) -> bool:
    def match(pats):
        return any(fnmatch.fnmatch(path, p) for p in pats)

    return match(PROTECTED) and not match(PROTECTED_OK)


VISUAL = re.compile(r"\b(viewer|screenshots?)\b", re.I)  # criteria the tester must see
# An app page named in a criterion ("/viewer/room", "/inspections/3", "/?signed-out"): never an
# API route (/api/..., /health) or a file (/openapi.json). Backend tasks say "the viewer must not
# break" with no page to look at: B1, S1 and DEF1-api lost their first plan to VISUAL (2026-10-23).
PAGE_PATH = re.compile(
    r"(?<![\w.:/])/(?!api\b|health\b)(?:[a-z][\w-]*(?:/[\w-]+)*|\?signed-out)(?![\w.-])"
)
# A whole API path, its {id} / <id> parts included: no word in it is a page (COV1, 2026-10-27:
# "GET /api/inspections/{id}/coverage" asked for routes because of "/coverage").
API_PATH = re.compile(r"/(?:api|health)\b[\w./{}<>:-]*")


def needs_routes(criteria: list, dev_cfg: dict):
    """The match that asks for screenshots, or None. The web developer's pages are its task: any
    mention of the viewer or screenshots. Others (pipeline): only a page path, e.g. /viewer/<run>.
    A developer without a screenshots config can list no routes at all."""
    sc = dev_cfg.get("screenshots")
    if not sc:
        return None
    text = API_PATH.sub(" ", " ".join(map(str, criteria)))
    web = "{runs}" not in sc.get("serve", "")  # pipeline tasks serve the quick benchmark output
    return (web and VISUAL.search(text)) or PAGE_PATH.search(text)


def stages_of(p: dict) -> list[dict]:
    """The parts the brain planned inside one task (owner, 2026-10-23: fewer, bigger tasks; a goal
    with more than ~4 build items is built in 2-3 parts, each accepted by the tester before the
    next starts). No parts: the whole plan is the one stage. The task's top-level criteria and
    routes (its final state) belong to the last part on top of that part's own."""
    parts = p.get("parts") or []
    if not parts:
        return [
            {
                "title": "",
                "plan": p.get("plan", ""),
                "criteria": list(p.get("criteria") or []),
                "routes": list(p.get("routes") or []),
            }
        ]
    out = [
        {
            "title": str(x.get("title", "")),
            "plan": str(x.get("plan", "")),
            "criteria": list(x.get("criteria") or []),
            "routes": list(x.get("routes") or []),
        }
        for x in parts
    ]
    last = out[-1]
    last["criteria"] += [c for c in p.get("criteria") or [] if c not in last["criteria"]]
    last["routes"] += [r for r in p.get("routes") or [] if r not in last["routes"]]
    return out


def stage_state(overview: str, stages: list[dict], k: int) -> dict:
    """What the developer and the tester see in part k: plan (the overview, the part, what is
    done already and what comes later), criteria and routes of this part only."""
    st = stages[k]
    if len(stages) == 1:
        return {"stage": k, "plan": overview, "criteria": st["criteria"], "routes": st["routes"]}
    n = len(stages)
    done = "\n".join(f"  {i + 1}. {s['title']}" for i, s in enumerate(stages[:k]))
    later = "\n".join(f"  {i + 1}. {s['title']}" for i, s in enumerate(stages[k + 1 :], k + 1))
    plan = (
        f"This task is built in {n} parts; the tester accepts each part before the next starts. "
        f"You are on part {k + 1} of {n}: {st['title']}.\n"
        + (f"Parts already done and accepted (their code is on this branch; build on it, do not "
           f"redo or rewrite it):\n{done}\n" if done else "")
        + (f"Later parts (not now):\n{later}\n" if later else "")
        + f"\nOverview of the whole task:\n{overview}\n\nPart {k + 1}: {st['plan']}"
    )  # fmt: skip
    return {"stage": k, "plan": plan, "criteria": st["criteria"], "routes": st["routes"]}


def e2e_output(out: str) -> str:
    """Playwright's list reporter: every test's line (passed, failed or skipped: the judge must
    see that a test ran rather than skipped) and the summary, not the server log."""
    lines = out.splitlines()
    tests = [x.strip() for x in lines if " › " in x]
    return "\n".join(tests + [x for x in lines[-40:] if " › " not in x])[-6000:]


# one attempt in the list reporter: "  ✘  3 [phone] › a.spec.ts:1:1 › name (retry #1) (3.0m)"
E2E_ATTEMPT = re.compile(r"\s*([✓✘-])\s+\d+ (.+?)(?: \(retry #\d+\))?(?: \([\d.]+[a-z]+\))?$")


def e2e_attempts(out: str) -> dict[str, list[tuple[str, str]]]:
    """Each test's attempts in order, (mark, line): Kerege runs Playwright with --retries=1."""
    tests: dict[str, list[tuple[str, str]]] = {}
    for x in out.splitlines():
        if m := E2E_ATTEMPT.match(x):
            tests.setdefault(m[2], []).append((m[1], x.strip()))
    return tests


def e2e_flaky(out: str) -> list[str]:
    """Tests that failed, then passed on the retry: they do not block a round, but they are
    listed in the packet, the PR and the daily report (owner, 2026-10-27: 24/24 late e2e
    failures passed on a re-run; one flake under load blocked a whole round)."""
    return [t for t, a in e2e_attempts(out).items() if a[-1][0] == "✓" and len(a) > 1]


def error_context(cwd: Path, rel: str, lines: int = 30) -> str:
    """The first lines of Playwright's error-context.md (error, page snapshot, test source).
    Only under `cwd`: the path comes from test output, which the developer's code can print."""
    path = (cwd / rel).resolve()
    if not path.is_relative_to(cwd.resolve()):
        return ""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            head = [x.rstrip() for _, x in zip(range(lines), f, strict=False)]
    except OSError:
        return ""
    return f"  {path.name} (first {lines} lines):\n" + "\n".join(f"    {x}" for x in head)


def e2e_report(out: str, timed_out: bool, minutes: int, cwd: Path | None = None) -> str:
    """What the developer gets for a failed e2e run (DS1, 2026-10-26: three blind rounds on a
    bare FAIL and the log's last 1500 characters, one of them a timeout): a plain TIMEOUT line
    with the counts, or each failing test's name, its first error lines and the head of its
    error-context.md (`cwd`: where Playwright ran, its paths are relative to it)."""
    tests = e2e_attempts(out)
    flaky = set(e2e_flaky(out))
    last = [a[-1] for t, a in tests.items() if t not in flaky]
    total = re.search(r"Running (\d+) tests?", out)
    passed = sum(m == "✓" for m, _ in last)
    failed = [x for m, x in last if m == "✘"]
    skipped = sum(m == "-" for m, _ in last)
    left = int(total[1]) - passed - len(flaky) - len(failed) - skipped if total else "?"
    flakes = f" Flaky (failed, then passed on retry; not blocking): {len(flaky)}." if flaky else ""
    if timed_out:
        head = (
            f"TIMEOUT: e2e stopped after {minutes} min (not a test failure); {passed} passed, "
            f"{len(failed)} failed, {left} not run.{flakes}"
        )
    else:
        head = f"e2e FAILED: {len(failed)} failed, {passed} passed.{flakes}"
    blocks, cur = [], None  # the list reporter's "  1) [project] › file:line › name" blocks
    for x in out.splitlines():
        if re.match(r"\s+\d+\) \[", x):
            name = x.strip().rstrip("─ ").split(") ", 1)[1]
            cur = {"lines": [x.strip().rstrip("─ ")], "ctx": None, "flaky": name in flaky}
            blocks.append(cur)
        elif cur is not None and re.match(r"\s+\d+ (failed|flaky|passed|skipped|did not run)", x):
            cur = None
        elif cur is not None and (m := re.match(r"\s+Error Context: (\S+)", x)):
            cur["ctx"] = cur["ctx"] or m[1]  # the first attempt's
        elif cur is not None and x.strip() and len(cur["lines"]) < 9:
            cur["lines"].append("  " + x.strip())
    detail = "\n\n".join(
        "\n".join(b["lines"])
        + (f"\n{ctx}" if cwd and b["ctx"] and (ctx := error_context(cwd, b["ctx"])) else "")
        for b in blocks
        if not b["flaky"]
    ) or "\n".join(failed)
    if flaky:
        detail += "\n\nFlaky (not blocking, fix when you can):\n" + "\n".join(sorted(flaky))
    return f"{head}\n{detail}".strip()[:12000]


def criterion_key(c: dict, criteria: list) -> str:
    """The same criterion in every round: its number in the plan, not the judge's wording
    (2026-10-19: P3e was sent back 3 times for one criterion, worded differently each time)."""
    n = c.get("number")
    if isinstance(n, int) and 1 <= n <= len(criteria):
        return f"criterion #{n}"
    return f"criterion: {c.get('criterion', '')}"


def limit_hint(task_id: str) -> str:
    return (
        ". Raise the `supervisor` limits in devteam/config.yaml (or the task's own `supervisor:` "
        "in backlog.yaml) or split the task, then "
        f"`devteam resume {task_id}`"
    )


def where(paths) -> str:
    """'3 file(s) in scratch/, web/': a log line, not a list of every path."""
    tops = sorted({p.split("/", 1)[0] + ("/" if "/" in p else "") for p in paths})
    return f"{len(paths)} file(s) in {', '.join(tops)}"


def resume_note(task: dict) -> str:
    """Said in the plan of a task whose branch already holds earlier work."""
    if not task.get("previous_work"):
        return ""
    return (
        f"Previous work is already on this task's branch: {task['previous_work']}. "
        "You continue on that branch (dev merged in): build on it, do not start over.\n\n"
    )


class PlanError(ValueError):
    """`outside`: task id -> folders its criteria need that the assignee may not edit.
    `too_big`: task id -> why it does not fit in MAX_PARTS parts. `only_split`: every error is
    one of those, so a second bad plan sends those tasks to needs_split."""

    def __init__(self, errors: list[str], outside: dict[str, list[str]], too_big=None, sizes=0):
        super().__init__("invalid plan: " + "; ".join(errors))
        self.outside, self.only_outside = outside, len(errors) == len(outside)
        self.too_big: dict[str, str] = too_big or {}
        self.only_split = len(errors) == len(outside) + sizes


SIZE_ERROR = "too big"


def outside_paths(criteria: list, dev: str, cfg: dict, task: dict) -> list[str]:
    """Developer folders (any developer's `owns`, or devteam/) named in the criteria that `dev`
    may not edit: such a task must be split first (A7 needed backend/ from dev_app)."""
    known = {SC.folder(d) for c in cfg["developers"].values() for d in c["owns"]} | {"devteam/"}
    mine = cfg["developers"][dev]["owns"] + list(task.get("writable") or [])
    near = tuple(SC.folder(d) for d in mine)  # "docs/notes/" names a folder dev_app may write
    alt = "|".join(map(re.escape, sorted(known)))
    pat = rf"(?<![\w./-])((?:{alt})[\w./-]*)"
    found = re.findall(pat, " ".join(map(str, criteria)))
    branch = re.compile(r"devteam/[A-Z][\w.-]*")  # a task branch like devteam/A7, not a folder
    return sorted(
        p
        for p in set(found)
        if not SC.owned(p, mine)
        and not p.startswith(near)
        and not branch.fullmatch(p)
        and not SC.REFERENCE_PNG.fullmatch(p.rstrip("."))  # a v8 screen to match, read-only
    )


def writes_runs(cfg: dict, task: dict) -> bool:
    """The task makes viewer runs (`writable: [data/runs]`, e.g. P3e): its screenshots show
    runs_dir, where it wrote them, not a benchmark output."""
    return cfg.get("runs_dir", "data/runs") in (task.get("writable") or [])


VIEWER_RUN = re.compile(r"^/viewer/([^/?#]+)")


def route_errors(p: dict, task: dict, cfg: dict) -> list[str]:
    """A /viewer/<run> route must show the task's own output (2026-10-19: P3e's routes named
    old runs, so the tester never saw the new ones). A task that writes runs: a run that was
    not there before the task (`runs_before_task`). Other pipeline tasks: a video of the quick
    benchmark their screenshots serve."""
    sc = cfg["developers"].get(p.get("assignee"), {}).get("screenshots") or {}
    if "{runs}" not in sc.get("serve", ""):
        return []  # web pages show runs_dir as it is: no run is the task's own
    tid, errors = p.get("task_id"), []
    before = set(task.get("runs_before_task") or [])
    quick = task.get("quick_only") or cfg["gates"]["benchmark"]["quick_only"]
    for route in p.get("routes") or []:
        if not (m := VIEWER_RUN.match(route)):
            continue
        run = m.group(1)
        if writes_runs(cfg, task) and run in before:
            errors.append(
                f"{tid}: route {route} shows run {run!r}, which was in "
                f"{cfg.get('runs_dir', 'data/runs')} before the task: name a run this task "
                "creates or changes (a new name, e.g. <video>-<suffix>)"
            )
        elif not writes_runs(cfg, task) and run not in quick:
            errors.append(
                f"{tid}: route {route} shows {run!r}, but this task's screenshots serve its quick "
                f"benchmark output: {quick}"
            )
    return errors


OWNER_ADDITIONS = re.compile(r"^Owner additions\b[^\n]*:[ \t]*$", re.M)


def owner_additions(goal: str) -> list[str]:
    """The `- ` lines under "Owner additions (<date>):" in a task's goal: each must reach the
    plan as a criterion, word for word (2026-10-21: the owner's lines are the spec)."""
    out = []
    for m in OWNER_ADDITIONS.finditer(goal or ""):
        for line in goal[m.end() :].splitlines():
            if (line := line.strip()).startswith("- "):
                out.append(line[2:].strip())
            elif line:
                break
    return out


DONE_WHEN = re.compile(r"^Done when:(.*?)(?=\n\n|\Z)", re.M | re.S)


def done_when(goal: str) -> list[str]:
    """The clauses of a goal's "Done when:" paragraph (split at ";"), one line each, no final
    full stop."""
    m = DONE_WHEN.search(goal or "")
    clauses = (" ".join(c.split()).rstrip(".") for c in (m.group(1).split(";") if m else []))
    return [c for c in clauses if c]


def listed_criteria(goal: str) -> int:
    """Checkable statements a backlog entry lists: the clauses of its "Done when:" paragraph
    (split at ";") plus its Owner additions. 0 without "Done when:": nothing to stay close to."""
    if not DONE_WHEN.search(goal or ""):
        return 0
    return len(done_when(goal)) + len(owner_additions(goal))


def _norm(text: str) -> str:
    return " ".join(str(text).lower().split())


def validate_plan(plans: list[dict], todo: list[dict], cfg: dict) -> list[dict]:
    """The whole plan is valid or nothing happens. Returns assignments."""
    by_id = {t["id"]: t for t in todo}
    devs = cfg["developers"]
    ids = [p.get("task_id") for p in plans]
    who = [p.get("assignee") for p in plans]
    errors, outside, too_big, sizes = [], {}, {}, 0
    if len(set(ids)) != len(ids):
        errors.append(f"task ids repeat: {ids}")
    if len(set(who)) != len(who):
        errors.append(f"a developer got more than one task: {who}")
    for p in plans:
        tid, dev = p.get("task_id"), p.get("assignee")
        if tid not in by_id:
            errors.append(f"{tid}: not an open task in the backlog")
        elif dev not in devs:
            errors.append(f"{tid}: unknown developer {dev}")
        elif by_id[tid].get("area") not in devs[dev]["areas"]:
            errors.append(
                f"{tid}: area {by_id[tid].get('area')!r} is not {dev}'s ({devs[dev]['areas']})"
            )
        elif bad := outside_paths(p.get("criteria") or [], dev, cfg, by_id[tid]):
            outside[tid] = bad
            errors.append(
                f"{tid}: the criteria need {bad}, which {dev} may not edit (it owns "
                f"{devs[dev]['owns']}). If the task must change them, do not plan it: "
                "list it in needs_split with the parts per developer. If they are only read, "
                "do not name them in the criteria"
            )
        accepted = (by_id.get(tid) or {}).get("accepted_parts") or []
        stages = stages_of(p)
        if not str(p.get("plan", "")).strip() or not all(st["criteria"] for st in stages):
            errors.append(
                f"{tid}: plan and acceptance criteria must not be empty"
                + (" (in every part)" if len(stages) > 1 else "")
            )
        if len(accepted) + len(stages) > MAX_PARTS:
            errors.append(
                f"{tid}: {len(accepted) + len(stages)} parts; plan at most {MAX_PARTS} inside one "
                "task, or list it in needs_split"
            )
        if again := sorted({x["title"] for x in accepted} & {st["title"] for st in stages}):
            errors.append(
                f"{tid}: parts {again} were accepted already and are kept: plan only the "
                "remaining parts"
            )
        size = size_errors(p, stages, accepted, by_id[tid]) if tid in by_id else []
        errors += [f"{tid}{e}" for e in size]
        if big := [e for e in size if SIZE_ERROR in e]:
            too_big[tid], sizes = f"{tid}{big[0]}", sizes + len(big)
        all_crit = [c for st in stages for c in st["criteria"]]
        crit = [_norm(c) for c in all_crit]
        if tid in by_id and (
            missing := [
                a
                for a in owner_additions(by_id[tid].get("goal", ""))
                if not any(_norm(a) in c for c in crit)
            ]
        ):
            errors.append(
                f"{tid}: the criteria drop {len(missing)} of the task's Owner additions; copy "
                f"each word for word as its own criterion: {missing}"
            )
        for k, st in enumerate(stages):
            if (m := needs_routes(st["criteria"], devs.get(dev, {}))) and not st["routes"]:
                part = f" (part {k + 1})" if len(stages) > 1 else ""
                errors.append(
                    f"{tid}{part}: the criteria name {m.group(0)!r} but list no routes "
                    "(the pages the tester screenshots)"
                )
        routes = [r for st in stages for r in st["routes"]]
        if routes and dev in devs and not devs[dev].get("screenshots"):
            errors.append(f"{tid}: routes given but {dev} has no screenshots config")
        elif tid in by_id and dev in devs:
            errors += route_errors({**p, "routes": routes}, by_id[tid], cfg)
    if errors:
        raise PlanError(errors, outside, too_big, sizes)
    out = []
    for p in plans:
        task = by_id[p["task_id"]]
        accepted = task.get("accepted_parts") or []
        stages = [*accepted, *stages_of(p)]
        if accepted and not stages[-1]["title"]:
            stages[-1]["title"] = "the rest of the task"
        overview = resume_note(task) + p["plan"]
        out.append(
            {
                "task": task,
                "assignee": p["assignee"],
                "overview": overview,
                "stages": stages,
                "part_base": task.get("part_base", "") if accepted else "",
                **stage_state(overview, stages, len(accepted)),
            }
        )
    return out


def size_errors(p: dict, stages: list[dict], accepted: list[dict], task: dict) -> list[str]:
    """At most MAX_PART_CRITERIA per part, FIRST_PART_CRITERIA in part 1 of several, and close
    to what the backlog entry lists: at most MAX_ADDED_CRITERIA more, each in `added_criteria`
    with why. Errors that mean "needs a split" say SIZE_ERROR; the caller adds the task id."""
    out = []
    for k, st in enumerate(stages, len(accepted) + 1):
        if len(st["criteria"]) > MAX_PART_CRITERIA:
            out.append(
                f" (part {k}): {len(st['criteria'])} criteria, {SIZE_ERROR} "
                f"(at most {MAX_PART_CRITERIA} per part)"
            )
    if not accepted and len(stages) > 1 and len(stages[0]["criteria"]) > FIRST_PART_CRITERIA:
        out.append(
            f" (part 1): {len(stages[0]['criteria'])} criteria, {SIZE_ERROR} (part 1 of several "
            f"stays small: at most {FIRST_PART_CRITERIA})"
        )
    listed = listed_criteria(task.get("goal", ""))
    total = len({c for st in stages for c in st["criteria"]})
    why = [a for a in p.get("added_criteria") or [] if str(a.get("why", "")).strip()]
    if listed and total > listed + MAX_ADDED_CRITERIA:
        out.append(
            f": {total} criteria for a backlog entry that lists {listed}, {SIZE_ERROR} "
            f"(at most {MAX_ADDED_CRITERIA} added)"
        )
    elif listed and total - listed > len(why):
        out.append(
            f": {total} criteria for a backlog entry that lists {listed}: explain each added "
            f"one in added_criteria (criterion, why); {len(why)} explained"
        )
    return out


class TaskState(TypedDict, total=False):
    task: dict
    assignee: str
    plan: str  # of the current part (stage_state)
    criteria: list[str]
    routes: list[str]  # web pages to screenshot for the tester
    overview: str  # the brain's plan for the whole task
    stages: list[dict]  # the parts the brain planned inside the task (one when it planned none)
    stage: int  # the part being built
    part_done: bool  # the tester accepted a part that is not the last: the next one follows
    part_base: str  # commit the previous part was accepted at ("" in part 1): real.part_base
    worktree: str
    branch: str
    session: str
    head: str  # last commit the graph accepted (checkpointed, so a crash can't move it)
    start: str  # HEAD when this run took the task: commits after it are this run's
    snap: dict  # protected-file hashes at the same moment
    dev_summary: str
    feedback: str
    rounds: int  # developer rounds done
    fixes: int  # times the work was sent back (by tester or gates)
    tokens: int  # subscription tokens the developer used on this task in this run
    last_sendback: str  # the tester's last reason (unmet criteria, failing checks); "" = passed
    bench_sendback: str  # HEAD the benchmark gate last sent back
    tested_head: str  # HEAD the tester last judged: the next round's test files are diffed to it
    after_dev: str  # "develop" after a resolve-conflicts step, else "test"
    verdict: dict
    gate: dict  # gate["blocked"]: the task ends blocked with gate["reason"]
    results: Annotated[list, operator.add]


class TeamState(TypedDict, total=False):
    developers: list[str]  # plan only for these (continuous mode: the one that is free)
    assignments: list[dict]
    splits: list[dict]  # {task_id, split}: tasks the brain says need splitting first
    results: Annotated[list, operator.add]


class Team:
    """Everything the nodes need: paths, config, secrets, agents, the remote."""

    def __init__(
        self,
        root: Path,
        cfg: dict,
        secrets: dict,
        agents: Any,
        fake: bool = False,
        remote: str | None = None,
        gh: Any = None,
    ):
        self.root, self.cfg, self.secrets, self.agents, self.fake = root, cfg, secrets, agents, fake
        self.runs_dir = root / cfg.get("runs_dir", "data/runs")  # QARAU_RUNS_DIR for every step
        self.home = root / "devteam" / ".state" / ("fake" if fake else "")
        self.home.mkdir(parents=True, exist_ok=True)
        self.branch_prefix = "devteam-fake" if fake else "devteam"
        real_backlog = root / "devteam" / "backlog.yaml"
        self.backlog_path = self.home / "backlog.yaml" if fake else real_backlog
        if fake and not self.backlog_path.exists():
            shutil.copy(real_backlog, self.backlog_path)
        self._lock = threading.Lock()  # ponytail: one lock for backlog + log, fine for 2 devs
        # Parallel tasks share one repo: `worktree add -b` writes .git/config, fetch locks refs.
        # Orchestrator git calls are short, so they simply take turns.
        self._git_lock = threading.Lock()
        self._procs: set[subprocess.Popen] = set()
        self._ctx = threading.local()  # .task: the task whose node runs in this thread
        self._status_commit = None  # the only commit push_status may push straight to dev
        self.only: set[str] = set()
        self.stopping = threading.Event()  # set: no node starts any more (clean stop)
        if remote is None and fake:
            remote = str(self.home / "remote.git")
            if not Path(remote).exists():
                self._seed_fake_remote(remote)
        self.remote_url = remote or "origin"
        self.remote_name = "origin" if self.remote_url == "origin" else "devteam-fake"
        self.gh = gh or (G.GitHub(self) if self.remote_url == "origin" else G.FakeGitHub(self))
        if self.remote_url == "origin":  # stand-in for branch protection, see hooks/pre-push
            hook, src = (
                Path(self.git("rev-parse", "--git-common-dir").strip()),
                Path(__file__).parents[1] / "hooks" / "pre-push",
            )
            hook = (hook if hook.is_absolute() else root / hook) / "hooks" / "pre-push"
            if not hook.exists() or hook.read_bytes() != src.read_bytes():
                shutil.copy(src, hook)
                hook.chmod(0o755)

    def _seed_fake_remote(self, path: str):
        """A local bare repo standing in for GitHub in --fake runs."""
        subprocess.run(["git", "init", "-q", "--bare", path], check=True)
        for b in (self.cfg["base_branch"], self.cfg["release_branch"]):
            src = next(
                r
                for r in (f"origin/{b}", b, "HEAD")
                if subprocess.run(
                    ["git", "rev-parse", "-q", "--verify", r],
                    cwd=self.root,
                    capture_output=True,
                ).returncode
                == 0
            )
            subprocess.run(
                ["git", "push", "-q", path, f"{src}:refs/heads/{b}"],
                cwd=self.root,
                check=True,
                capture_output=True,
            )

    # --- logging / live view -------------------------------------------------------------
    def redact(self, text: str) -> str:
        for v in self.secrets.values():
            if v and len(v) > 8:
                text = text.replace(v, "***")
        return text

    def emit(self, agent: str, msg: str, *, task: str = "", to: str = "", status: str = "") -> dict:
        event = {
            "t": time.strftime("%H:%M:%S"),
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),  # the dashboard needs the day too
            "agent": agent,
            "task": task,
            "to": to,
            "status": status,
            "msg": procs.clean_text(self.redact(msg)),
        }
        with (
            self._lock,
            open(self.home / "events.log", "a", encoding="utf-8", errors="replace") as f,
        ):
            f.write(yaml.safe_dump([event], allow_unicode=True, width=1000))
        try:
            get_stream_writer()(event)
        except RuntimeError:  # called outside a graph run
            pass
        return event

    # --- backlog -------------------------------------------------------------------------
    def load_backlog(self) -> list[dict]:
        return yaml.safe_load(self.backlog_path.read_text())["tasks"]

    def update_task(self, task_id: str, **fields):
        was = ""
        with self._lock:
            data = yaml.safe_load(self.backlog_path.read_text())
            for t in data["tasks"]:
                if t["id"] == task_id:
                    was = t["status"]
                    for k, v in fields.items():  # None removes the field
                        if v is None:
                            t.pop(k, None)
                        else:
                            t[k] = v
            self._write_backlog(data)
        why = str(fields.get("blocked_reason") or "")
        newly = fields.get("status") == "blocked" and was != "blocked"
        if newly and not why.startswith(OWNER_STOP):  # the owner knows about their own stop
            watchdog.notify(self, f"{task_id} blocked: {why[:250]}")

    def _write_backlog(self, data: dict):
        """Under self._lock. Readers don't take the lock: the file is replaced whole, never
        truncated in place."""
        tmp = self.home / "backlog.yaml.tmp"  # same filesystem: os.replace is atomic
        tmp.write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=100),
            encoding="utf-8",
            errors="replace",
        )
        os.replace(tmp, self.backlog_path)

    # --- processes (all killable by `devteam stop`) --------------------------------------
    def run_proc(
        self,
        cmd,
        cwd: str,
        timeout: int,
        input: str | None = None,
        env: dict | None = None,
        agent: str = "tester",
        watch=None,
        again: bool = True,
    ) -> dict:
        """Runs in its own process group, registered so stop() can kill the whole tree. When it
        ends (any way), everything it started is killed; a port it left listening is a warning
        (owner rule: no agent leaves a server running).
        Every POLL_S: `devteam stop <task>` for the task of this thread, and `watch(new stdout
        bytes, seconds so far, CPU seconds by pid every CPU_EVERY_S or None)`; a reason from
        either kills the step: TaskHalted.
        A step (not a watched developer run) that failed while the machine slept (the wall clock
        ran over SLEPT_S ahead of the monotonic one, which stops in sleep) runs once more: its
        servers and timeouts saw the clock jump (2026-10-27: a check failed across a Mac sleep)."""
        wall0, mono0 = time.time(), time.monotonic()
        marker, task, halted = procs.new_marker(), getattr(self._ctx, "task", ""), ""
        env = {**(os.environ if env is None else env), procs.MARKER: marker}
        # Output goes to files, not pipes: a server the step leaves in the background keeps a
        # pipe open, and reading it to EOF would wait for the timeout instead of the step.
        with tempfile.TemporaryFile() as fo, tempfile.TemporaryFile() as fe:
            p = subprocess.Popen(
                cmd,
                shell=isinstance(cmd, str),
                cwd=cwd,
                stdin=subprocess.PIPE if input else subprocess.DEVNULL,
                stdout=fo,
                stderr=fe,
                start_new_session=True,
                env=env,
            )
            with self._lock:
                self._procs.add(p)
            step = self.home / "steps" / f"{p.pid}-{marker}"  # a killed orchestrator's leftovers
            step.parent.mkdir(exist_ok=True)  # are reaped by the watchdog before it resumes
            step.touch()
            if input:
                threading.Thread(target=_feed, args=(p.stdin, input), daemon=True).start()
            note, t0, pos, cpu_at = "", time.monotonic(), 0, -CPU_EVERY_S
            try:
                while True:
                    try:
                        p.wait(timeout=POLL_S)
                        break
                    except subprocess.TimeoutExpired:
                        pass
                    if (secs := time.monotonic() - t0) >= timeout:
                        note = f"\ntimed out after {timeout // 60} min"
                        break
                    if task and (halted := self.stop_reason(task)):
                        break
                    if watch:
                        new = b""  # pread: the child shares the file offset, never seek it
                        while chunk := os.pread(fo.fileno(), 1 << 20, pos + len(new)):
                            new += chunk
                        pos += len(new)
                        cpu = None
                        if secs - cpu_at >= CPU_EVERY_S:
                            cpu, cpu_at = procs.cpu_times(p.pid, marker), secs
                        if halted := watch(new, secs, cpu):
                            break
            finally:
                with self._lock:
                    self._procs.discard(p)
                for port, pid in procs.reap(p.pid, marker):  # the whole step, any outcome
                    label = cmd if isinstance(cmd, str) else " ".join(cmd[:3])
                    self.emit(
                        agent,
                        f"WARNING: `{label[:80]}` left port {port} listening (pid {pid}); "
                        "killed it",
                    )
                step.unlink(missing_ok=True)
                p.wait()
            fo.seek(0)
            fe.seek(0)
            out = fo.read().decode("utf-8", "replace")
            err = fe.read().decode("utf-8", "replace") + note
        if halted:
            self.emit(agent, f"killed: {halted}", task=task)
            raise TaskHalted(halted, out)
        ok = p.returncode == 0 and not note
        slept = (time.time() - wall0) - (time.monotonic() - mono0)
        if not ok and again and watch is None and slept > SLEPT_S:
            label = cmd if isinstance(cmd, str) else " ".join(cmd[:3])
            self.emit(
                agent,
                f"the machine slept {slept / 60:.0f} min during `{label[:80]}`, which failed: "
                "running it once more instead of failing the round",
                task=task,
            )
            return self.run_proc(cmd, cwd, timeout, input, env, agent, watch, again=False)
        return {
            "ok": ok,
            "timed_out": bool(note),
            "code": p.returncode,
            "stdout": out,
            "stderr": err,
            "output": out + err,
        }

    def kill_all(self):
        with self._lock:
            for p in list(self._procs):
                try:
                    os.killpg(p.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass

    # --- owner control: `devteam stop <task>` / `devteam resume <task>` -------------------
    # The CLI leaves a file; a running orchestrator reads it (every POLL_S in a step, at node
    # start, in the brain). With no run active the CLI applies it directly.
    def control_file(self, kind: str, task_id: str) -> Path:
        return self.home / "control" / f"{kind}-{task_id}"

    def request(self, kind: str, task_id: str):
        other = {"stop": "resume", "resume": "stop"}[kind]
        self.control_file(other, task_id).unlink(missing_ok=True)
        f = self.control_file(kind, task_id)
        f.parent.mkdir(exist_ok=True)
        f.write_text(time.strftime("%Y-%m-%dT%H:%M:%S"))

    def stop_reason(self, task_id: str) -> str:
        if self.control_file("stop", task_id).exists():
            return f"{OWNER_STOP} (`devteam stop {task_id}`); `devteam resume {task_id}` continues"
        return ""

    def apply_controls(self, running: bool = True) -> list[str]:
        """Stop / resume requests for tasks not in a step right now. `running`: an orchestrator
        is active, so an in-progress task stops in its own step (and gets its packet)."""
        out = []
        for f in sorted((self.home / "control").glob("*-*")):
            kind, tid = f.name.split("-", 1)
            t = next((t for t in self.load_backlog() if t["id"] == tid), None)
            if t is None or t["status"] in ("done", "dropped"):
                f.unlink()
                out.append(f"{tid}: {'unknown task' if t is None else t['status']}, nothing to do")
            elif kind == "resume":
                f.unlink()
                if t["status"] == "blocked":
                    self.update_task(tid, status="todo", blocked_reason=None)
                    self.push_status(tid, f"backlog: {tid} todo (resumed by the owner)")
                    out.append(f"{tid}: back to todo; it continues on its branch when planned")
                else:
                    out.append(f"{tid}: {t['status']}, nothing to resume")
            elif t["status"] == "todo" or (t["status"] == "in_progress" and not running):
                self.update_task(tid, status="blocked", blocked_reason=self.stop_reason(tid))
                self.push_status(tid, f"backlog: {tid} blocked (stopped by the owner)")
                if t["status"] == "todo":  # an in-progress one keeps it: a resumed round stops
                    f.unlink()
                out.append(f"{tid}: blocked, stopped by the owner")
        for line in out:
            self.emit("brain", line)
        return out

    def violation(self, task_id: str, agent: str, kind: str, detail: str):
        """Scope / protected / bypass violations: kept for the dashboard and the report."""
        row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "task": task_id, "agent": agent}
        row.update(kind=kind, detail=self.redact(detail)[:1000])
        with self._lock, open(self.home / "violations.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
        self.emit("tester", f"VIOLATION {kind}: {detail[:300]}", task=task_id)

    def violations(self, day: str, log: str = "violations.jsonl") -> list[dict]:
        try:
            lines = (self.home / log).read_text().splitlines()
        except FileNotFoundError:
            return []
        return [r for r in map(json.loads, lines) if r["ts"].startswith(day)]

    def flaky(self, task_id: str, tests: list[str]):
        """e2e tests that failed, then passed on the retry: not blocking, kept for the report."""
        row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "task": task_id, "tests": tests}
        with self._lock, open(self.home / "flaky.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
        self.emit("tester", f"FLAKY e2e (passed on retry, not blocking): {tests[:5]}", task=task_id)

    # --- parts the tester accepted: kept when the task is planned again (resume) ------------
    def accept_parts(self, task: dict, parts: list[dict], base: str):
        """After the tester accepts a part that is not the last (DS1, 2026-10-26: a resume
        re-planned part 1, accepted the day before, and spent a round on it). `base`: the commit
        it was accepted at, where the next part's judged diff starts (real.part_base)."""
        f = self.home / "parts" / f"{task['id']}.json"
        f.parent.mkdir(exist_ok=True)
        kept = {"goal": task.get("goal", ""), "parts": parts, "base": base}
        f.write_text(json.dumps(kept, indent=1))

    def accepted_parts(self, task: dict) -> tuple[list[dict], str]:
        """The accepted parts of an earlier run and the commit the last one was accepted at, or
        ([], "") (none, or the goal changed since: then the whole task is planned again, and
        the events log says why)."""
        f = self.home / "parts" / f"{task['id']}.json"
        try:
            kept = json.loads(f.read_text())
        except FileNotFoundError:
            return [], ""
        if kept["goal"] != task.get("goal", ""):
            f.unlink()
            titles = [x["title"] for x in kept["parts"]]
            self.emit(
                "brain",
                f"the goal changed since parts {titles} were accepted: planning the whole task "
                "again",
                task=task["id"],
            )
            return [], ""
        return kept["parts"], kept.get("base", "")

    def tester_error(self, task_id: str, detail: str):
        """The tester's verdict stayed incomplete after one re-ask: the round does not pass, and
        it is not counted against the developer. Kept for the report and the dashboard."""
        row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "task": task_id, "detail": detail}
        with self._lock, open(self.home / "tester_errors.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
        self.emit(
            "tester", f"TESTER ERROR (not counted against the developer): {detail}", task=task_id
        )

    @contextlib.contextmanager
    def clean_checkout(self, s):
        """A fresh detached checkout of the task's committed HEAD: the tester's checks never
        see the developer's untracked or ignored leftovers."""
        path = self.home / "check-wt" / s["task"]["id"]
        if path.exists():
            self.git("worktree", "remove", "--force", str(path))
        self.git("worktree", "add", "--detach", str(path), s["head"])
        try:
            yield str(path)
        finally:
            self.git("worktree", "remove", "--force", str(path))

    # --- the task branch -----------------------------------------------------------------
    def merge_base(self, wt: str) -> list[str]:
        """Merges the latest dev into the task branch (a merge, never a rebase; the orchestrator
        does it, not the agent). Returns the conflicting files with the merge left in progress
        for a resolve step, [] when it merged cleanly or was up to date."""
        base = self.cfg["base_branch"]
        self.fetch(base)
        r = subprocess.run(
            ["git", "merge", "--no-edit", f"{self.remote_name}/{base}"],
            cwd=wt,
            capture_output=True,
            text=True,
        )
        if not r.returncode:
            return []
        out = self.git("diff", "--name-only", "-z", "--diff-filter=U", cwd=wt)
        if files := [f for f in out.split("\0") if f]:
            return files
        subprocess.run(["git", "merge", "--abort"], cwd=wt, capture_output=True)
        raise TaskHalted(f"could not merge {base} into the task branch: {r.stderr[-400:]}")

    def writes_runs(self, task: dict) -> bool:
        return writes_runs(self.cfg, task)

    def runs_before_task(self, task: dict) -> list[str]:
        """Run ids in runs_dir when the task started (its data snapshot)."""
        rel = Path(self.cfg.get("runs_dir", "data/runs"))
        return sorted({Path(k).relative_to(rel).parts[0] for k in self.data_at_start(task)})

    def dev_merge(self, wt: str, head: str) -> bool:
        """HEAD is dev merged onto `head` (a fast-forward, or a clean merge commit): what
        develop() does before a round. A crash after it (2026-10-18 23:39) left HEAD there while
        the checkpoint kept `head`; the next round then counted dev's changes (backlog.yaml
        status commits too) as the developer's. A merge commit's tree must equal git's own
        merge, so no other change rides along."""
        dev = f"{self.remote_name}/{self.cfg['base_branch']}"

        def ancestor(a: str, b: str) -> bool:
            r = subprocess.run(["git", "merge-base", "--is-ancestor", a, b], cwd=wt)
            return r.returncode == 0

        if ancestor(head, "HEAD") and ancestor("HEAD", dev):
            return True  # fast-forward: nothing on the branch but dev's own commits
        parents = self.git("rev-list", "--parents", "-n1", "HEAD", cwd=wt).split()[1:]
        if len(parents) != 2 or parents[0] != head or not ancestor(parents[1], dev):
            return False
        merged = subprocess.run(  # exit 1 on conflicts: a resolved merge is not "clean"
            ["git", "merge-tree", "--write-tree", *parents], cwd=wt, capture_output=True, text=True
        )
        tree = self.git("rev-parse", "HEAD^{tree}", cwd=wt).strip()
        return not merged.returncode and merged.stdout.strip() == tree

    def data_snapshot(self, task: dict) -> dict[str, list]:
        """Files in the task's `writable` data folders (e.g. data/runs): a task may add new ones
        there, never change or delete one that was there when it started."""
        snap = {}
        for d in task.get("writable") or []:
            for dirpath, _dirs, names in os.walk(self.root / d):
                for n in names:
                    st = os.lstat(os.path.join(dirpath, n))
                    snap[os.path.relpath(os.path.join(dirpath, n), self.root)] = [
                        st.st_size,
                        st.st_mtime_ns,
                    ]
        return snap

    def data_at_start(self, task: dict) -> dict[str, list]:
        """data_snapshot when this task started (kept in a file until it finishes): its own
        new files from an earlier round may change, what was there before may not."""
        f = self.home / "data-snap" / f"{task['id']}.json"
        if not f.exists():
            f.parent.mkdir(exist_ok=True)
            f.write_text(json.dumps(self.data_snapshot(task)))
        return json.loads(f.read_text())

    # --- git -----------------------------------------------------------------------------
    def _guard(self, args: tuple[str, ...]):
        cmd = args[0] if args else ""
        if cmd in FORBIDDEN_GIT:
            raise PermissionError(f"forbidden for agents: git {cmd}")
        if cmd == "push":
            refs = [a for a in args[1:] if not a.startswith("-")][1:]  # after the remote
            if (
                FORBIDDEN_PUSH & set(args)
                or any(a.startswith("--force") for a in args)
                or not refs
                or any(r.startswith(("+", ":")) for r in refs)
                or any(
                    not r.startswith(self.branch_prefix + "/")
                    and not RELEASE_TAG.fullmatch(r)
                    and r != f"{self._status_commit}:refs/heads/{self.cfg['base_branch']}"
                    for r in refs
                )
            ):
                raise PermissionError(
                    f"forbidden push: git {' '.join(args)} (only "
                    f"{self.branch_prefix}/<task> or a new release tag, never forced)"
                )
        if cmd == "branch" and FORBIDDEN_BRANCH & set(args):
            raise PermissionError(f"forbidden for agents: git {' '.join(args)}")

    def git(
        self,
        *args: str,
        cwd: Path | str | None = None,
        input: str | None = None,
        env: dict | None = None,
    ) -> str:
        self._guard(args)
        # A network command that cannot reach the remote is retried after GIT_NET_WAITS before
        # it fails (2026-10-26: one DNS drop on `git fetch` blocked DS1 as a crash).
        for wait in (*GIT_NET_WAITS, None) if args[0] in GIT_NET_COMMANDS else (None,):
            with self._git_lock:
                r = subprocess.run(  # bytes, not text=True: \r in file names would become \n
                    ["git", *args],
                    cwd=cwd or self.root,
                    capture_output=True,
                    input=input.encode("utf-8", "replace") if input is not None else None,
                    env={**os.environ, "GIT_LITERAL_PATHSPECS": "1", **(env or {})},
                )
            err = r.stderr.decode(errors="replace")
            if not r.returncode or wait is None or not GIT_NET_ERROR.search(err):
                break
            self.emit("brain", f"git {args[0]}: network error, retrying in {wait}s: {err[-200:]}")
            time.sleep(wait)
        if r.returncode:
            raise RuntimeError(f"git {' '.join(args)}: {r.stderr.decode(errors='replace').strip()}")
        # never surrogateescape: a binary file in a diff reached the judge's prompt as lone
        # surrogates (2026-10-21). ponytail: a non-UTF-8 file name no longer round-trips to git;
        # APFS has none, a Linux runner would fail that git call loudly.
        return r.stdout.decode("utf-8", "replace")

    def fetch(self, *branches: str):
        specs = [f"+refs/heads/{b}:refs/remotes/{self.remote_name}/{b}" for b in branches]
        self.git("fetch", "-q", self.remote_url, *specs)

    def fetch_tasks(self):
        """dev and every devteam/<id> branch on the remote."""
        rn, base, pre = self.remote_name, self.cfg["base_branch"], self.branch_prefix
        self.git(
            "fetch",
            "-q",
            self.remote_url,
            f"+refs/heads/{base}:refs/remotes/{rn}/{base}",
            f"+refs/heads/{pre}/*:refs/remotes/{rn}/{pre}/*",
        )

    def merged_tasks(self) -> set[str]:
        """Task ids whose devteam/<id> branch on the remote is already merged into dev."""
        rn, base, pre = self.remote_name, self.cfg["base_branch"], self.branch_prefix
        self.fetch_tasks()
        out = self.git("branch", "-r", "--merged", f"{rn}/{base}", "--list", f"{rn}/{pre}/*")
        return {line.strip().rsplit("/", 1)[1] for line in out.splitlines() if line.strip()}

    def prior_work(self, task_id: str) -> str:
        """Commits on devteam/<id> (local first, then the remote's) that dev does not have.
        Call after fetch_tasks()."""
        b, base = self.branch_for(task_id), f"{self.remote_name}/{self.cfg['base_branch']}"
        for ref in self.git(
            "for-each-ref",
            "--format=%(refname:short)",
            f"refs/heads/{b}",
            f"refs/remotes/{self.remote_name}/{b}",
        ).split():
            log = self.git("log", "--oneline", "--no-merges", f"{base}..{ref}").splitlines()
            if log:
                return f"{len(log)} commit(s) on {b} not in dev yet, latest: {log[0]}"
        return ""

    def unblock_ready(self) -> list[str]:
        """Blocked tasks waiting on their depends_on (waits_on_dependencies) go back to todo when
        those are all done, pushed to dev. Any other blocked task waits for `devteam resume`."""
        tasks = self.load_backlog()
        done = {t["id"] for t in tasks if t["status"] == "done"}
        ready = [
            t["id"]
            for t in tasks
            if t["status"] == "blocked"
            and waits_on_dependencies(t)
            and set(t["depends_on"]) <= done
        ]
        for tid in ready:
            self.update_task(tid, status="todo", blocked_reason=None)
            self.push_status(tid, f"backlog: {tid} todo (dependencies done)")
            self.emit("brain", "dependencies done, back to todo", task=tid)
        return ready

    def push_status(self, task_id: str, message: str) -> str | None:
        """Commits this task's status fields onto origin/dev and pushes them straight to dev:
        dev holds the record (a status kept only in the working copy got lost on 2026-10-16).
        Returns None when the backlog tests refused it (push_backlog)."""

        def edit(data: dict, _raw: str):
            mine = next(t for t in self.load_backlog() if t["id"] == task_id)
            for t in data["tasks"]:
                if t["id"] == task_id:
                    for k in (*STATUS_FIELDS, "blocked_reason"):
                        if k in mine:
                            t[k] = mine[k]
                        else:
                            t.pop(k, None)

        return message if self.push_backlog(edit, message, task_id) is not None else None

    def change_backlog(self, edit, message: str, task_id: str = "") -> str | None:
        """A plan change (Analyst, Architect, undo): pushed to dev as its own commit (the
        checkpoint `devteam undo <commit>` reverts), then made in this backlog file too. `edit`
        must give the same result when run again (the checkout may already have it). Returns the
        commit, or None when the backlog tests refused it (then nothing changed anywhere)."""
        sha = self.push_backlog(edit, message, task_id)
        if sha is None:
            return None
        with self._lock:
            raw = self.backlog_path.read_text()
            data = yaml.safe_load(raw)
            text = edit(data, raw)
            self._write_backlog(data if text is None else yaml.safe_load(text))
        return sha

    def undo(self, commit: str) -> str:
        """`devteam undo <commit>`: a new commit that reverts one plan change; never a history
        rewrite. Entries it added go (unless started since); fields it changed get their old
        value back unless they changed again since (a later status stays). Returns the commit."""
        self.fetch(self.cfg["base_branch"])
        path, sha = "devteam/backlog.yaml", self.git("rev-parse", "--verify", commit).strip()
        subject = self.git("log", "-1", "--format=%s", sha).strip()
        if not subject.startswith(PLAN_CHANGE):
            raise ValueError(f"{sha[:9]} is not a plan change ({PLAN_CHANGE}...): {subject}")
        if self.git("diff-tree", "--no-commit-id", "--name-only", "-r", sha).split() != [path]:
            raise ValueError(f"{sha[:9]} changes more than {path}")
        before, after = (
            {t["id"]: t for t in yaml.safe_load(self.git("show", f"{sha}{x}:{path}"))["tasks"]}
            for x in ("^", "")
        )
        if gone := before.keys() - after.keys():
            raise ValueError(f"{sha[:9]} removed {sorted(gone)}: undo it by hand")

        def edit(data: dict, _raw: str):
            for t in list(data["tasks"]):
                if t["id"] not in before and t["id"] in after:  # added by the change
                    if t["status"] not in ("todo", "blocked"):
                        raise ValueError(f"{t['id']} is {t['status']} now: undo it by hand")
                    data["tasks"].remove(t)
                elif t["id"] in before and t["id"] in after:
                    b, a = before[t["id"]], after[t["id"]]
                    for k in b.keys() | a.keys():
                        if b.get(k) != a.get(k) and t.get(k) == a.get(k):  # not changed since
                            if k in b:
                                t[k] = b[k]
                            else:
                                t.pop(k, None)

        out = self.change_backlog(edit, f"backlog: undo {sha[:9]} ({subject})"[:200])
        if out is None:
            raise ValueError("the backlog tests refuse the undo: see the events log")
        self.emit("brain", f"undid {sha[:9]}: {subject}")
        return out

    def push_backlog(self, edit, message: str, task_id: str = "") -> str | None:
        """`edit(data, raw)` on the backlog of a fresh origin/dev (it changes `data`, or returns
        the new text), committed and pushed straight to dev. Never forced or rebased: a race
        costs a retry. The pre-push hook lets through only a fast-forward that changes
        backlog.yaml alone. The backlog tests run on the new backlog first: if they fail,
        nothing is pushed and the owner is told why; returns None then, else the commit."""
        base, rn, err, refused, pushed = self.cfg["base_branch"], self.remote_name, None, "", ""
        with self._lock:  # no emit() inside: it takes the same lock
            for attempt in range(3):
                self.fetch(base)
                parent = self.git("rev-parse", f"{rn}/{base}").strip()
                raw = self.git("show", f"{parent}:devteam/backlog.yaml")
                data = yaml.safe_load(raw)
                text = edit(data, raw)
                if text is None:
                    text = yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=100)
                if refused := self.backlog_check(text):
                    break
                blob = self.git("hash-object", "-w", "--stdin", input=text).strip()
                env = {"GIT_INDEX_FILE": str(self.home / "status.index")}
                self.git("read-tree", parent, env=env)
                self.git(
                    "update-index", "--cacheinfo", f"100644,{blob},devteam/backlog.yaml", env=env
                )
                tree = self.git("write-tree", env=env).strip()
                if tree == self.git("rev-parse", f"{parent}^{{tree}}").strip():
                    pushed = parent
                    break  # dev already says so (a resumed finish)
                self._status_commit = self.git(
                    "commit-tree", tree, "-p", parent, "-m", message
                ).strip()
                try:
                    self.git(
                        "push", "-q", self.remote_url, f"{self._status_commit}:refs/heads/{base}"
                    )
                    pushed = self._status_commit
                    break
                except RuntimeError:  # dev moved meanwhile: rebuild on the new tip
                    if attempt == 2:
                        raise
                finally:
                    self._status_commit = None
            if refused:
                pass  # nothing was pushed: the checkout stays as it is
            elif pushed and not self.fake and self.git("branch", "--show-current").strip() == base:
                # to the commit just pushed: a push by URL leaves origin/dev behind, so the
                # checkout stayed one status behind with backlog.yaml modified (2026-10-18)
                try:  # keep the main checkout on dev in step; local edits ride along
                    self.git("merge", "-q", "--ff-only", "--autostash", pushed)
                except RuntimeError as e:
                    err = e
        if refused:
            self.emit(
                "brain",
                f"WARNING: `{message}` not pushed to {base}: the backlog tests fail on it, "
                f"so it would turn CI red. Fix the test or the backlog by hand:\n{refused}",
                task=task_id,
            )
            return None
        if err:
            self.emit("brain", f"could not fast-forward the checkout: {err}; pull by hand")
        return pushed

    def backlog_check(self, text: str) -> str:
        """gates.backlog_check on the backlog a status commit would push (DEVTEAM_BACKLOG):
        the failing output, or "" (owner, 2026-10-18: 778e072 `backlog: A9-fix done (#41)`
        turned dev CI red). Not run_proc: it is called under self._lock."""
        cmd = self.cfg["gates"].get("backlog_check")
        if not cmd or self.fake:
            return ""
        path = self.home / "backlog-check.yaml"
        path.write_text(text, encoding="utf-8", errors="replace")
        r = subprocess.run(
            cmd,
            shell=True,
            cwd=self.root / "devteam",
            env={**os.environ, "DEVTEAM_BACKLOG": str(path)},
            capture_output=True,
            text=True,
            timeout=600,
        )
        return "" if r.returncode == 0 else self.redact((r.stdout + r.stderr)[-1500:])

    def backlog_drift(self) -> list[str]:
        """Status fields that differ between the backlog file and the one on origin/dev."""
        base = self.cfg["base_branch"]
        self.fetch(base)
        theirs = yaml.safe_load(
            self.git("show", f"{self.remote_name}/{base}:devteam/backlog.yaml")
        )["tasks"]

        def fields(tasks):
            return {t["id"]: {k: t.get(k) for k in STATUS_FIELDS} for t in tasks}

        mine, theirs = fields(self.load_backlog()), fields(theirs)
        return [
            f"{tid}.{k}: {theirs.get(tid, {}).get(k)!r} on {base} -> "
            f"{mine.get(tid, {}).get(k)!r} here"
            for tid in sorted(mine.keys() | theirs.keys())
            for k in STATUS_FIELDS
            if mine.get(tid, {}).get(k) != theirs.get(tid, {}).get(k)
        ]

    def has_ref(self, ref: str) -> bool:
        try:
            self.git("rev-parse", "--verify", "-q", f"{ref}^{{commit}}")
            return True
        except RuntimeError:
            return False

    def branch_for(self, task_id: str) -> str:
        return f"{self.branch_prefix}/{task_id}"

    def verify_worktree(self, s: TaskState):
        """The worktree must be registered with git and sit on exactly devteam/<task>."""
        wt, branch = Path(s["worktree"]).resolve(), self.branch_for(s["task"]["id"])
        if branch in (self.cfg["base_branch"], self.cfg["release_branch"], "main", "master"):
            raise Violation(f"refusing to work on {branch}")
        registered, current = {}, None
        for line in self.git("worktree", "list", "--porcelain").splitlines():
            if line.startswith("worktree "):
                current = Path(line[9:]).resolve()
            elif line.startswith("branch ") and current:
                registered[current] = line[7:]
        if registered.get(wt) != f"refs/heads/{branch}":
            raise Violation(
                f"{wt} is not a registered worktree on {branch} "
                f"(git says: {registered.get(wt, 'not registered')})"
            )
        r = subprocess.run(
            ["git", "symbolic-ref", "--quiet", "HEAD"], cwd=wt, capture_output=True, text=True
        )
        if r.stdout.strip() != f"refs/heads/{branch}":
            raise Violation(
                f"{wt} is on {r.stdout.strip() or 'a detached HEAD'}, expected {branch}"
            )

    def snapshot(self, wt: str) -> dict[str, str]:
        """Content hash of every protected file, tracked or ignored.
        ponytail: skips SNAPSHOT_SKIP dirs (node_modules, .venv) for speed."""
        snap = {}
        for dirpath, dirnames, filenames in os.walk(wt):
            dirnames[:] = [d for d in dirnames if d not in SNAPSHOT_SKIP]
            for name in filenames:
                full = os.path.join(dirpath, name)
                rel = os.path.relpath(full, wt)
                if is_protected(rel):
                    if os.path.islink(full):
                        snap[rel] = "link:" + os.readlink(full)
                    else:
                        with open(full, "rb") as f:
                            snap[rel] = hashlib.file_digest(f, "sha256").hexdigest()
        return snap

    def _revert(self, wt: str, paths, snap_before: dict):
        for path in sorted(paths):
            in_head = (
                subprocess.run(
                    ["git", "cat-file", "-e", f"HEAD:{path}"], cwd=wt, capture_output=True
                ).returncode
                == 0
            )
            if in_head:
                self.git("checkout", "HEAD", "--", path, cwd=wt)
            elif path not in snap_before:  # new file: safe to delete
                (Path(wt) / path).unlink(missing_ok=True)

    def commit_round(self, s: TaskState, head_before: str, snap_before: dict) -> dict:
        """Commit the developer's changes in its own folders, staging only checked paths, and
        only if no secret is found. Returns {"refused": [...], "secrets": [...]}."""
        self.verify_worktree(s)
        wt = s["worktree"]
        owns = self.cfg["developers"][s["assignee"]]["owns"]
        if self.git("rev-parse", "HEAD", cwd=wt).strip() != head_before:
            self.git("reset", "-q", "--soft", head_before, cwd=wt)  # its commits get checked too
        self.git("reset", "-q", cwd=wt)  # unstage everything; we decide what goes in

        snap_after = self.snapshot(wt)
        changed = {
            p for p in set(snap_before) | set(snap_after) if snap_before.get(p) != snap_after.get(p)
        }
        refused = set(changed)

        # Everything into a throwaway index, so git sees renames (both paths) and new files.
        # The real index only ever gets the validated paths.
        tmp_index = {"GIT_INDEX_FILE": str(self.home / f"index-{s['task']['id']}")}
        self.git("read-tree", "HEAD", cwd=wt, env=tmp_index)
        self.git("add", "-A", cwd=wt, env=tmp_index)
        entries = self.git("status", "--porcelain=v1", "-z", cwd=wt, env=tmp_index).split("\0")
        os.unlink(tmp_index["GIT_INDEX_FILE"])
        allowed, i = [], 0
        while i < len(entries):
            e = entries[i]
            i += 1
            if not e:
                continue
            code, paths = e[:2], [e[3:]]
            if "R" in code or "C" in code:  # rename/copy: next entry is the source path
                paths.append(entries[i])
                i += 1
            ok = all(SC.owned(p, owns) and not is_protected(p) for p in paths)
            if ok:
                allowed.extend(paths)
            else:
                refused.update(paths)

        self._revert(wt, refused, snap_before)
        # A protected file that existed before (ignored, so git has no copy) was changed or
        # deleted: we can't restore it and won't delete it. Stop.
        lost = sorted(
            p for p in changed if p in snap_before and self.git("ls-files", "--", p, cwd=wt) == ""
        )
        if lost:
            raise Violation(
                f"developer changed protected files git can't restore: {lost}. "
                "Restore them by hand, then `devteam resume`."
            )

        allowed = [p for p in allowed if p not in refused]
        secrets = []
        if allowed:
            self.git(
                "add",
                "-A",
                "--pathspec-from-file=-",
                "--pathspec-file-nul",
                cwd=wt,
                input="\0".join(allowed),
            )
            leaks = G.gitleaks(self, wt, "--pre-commit", "--staged")
            secrets = sorted({f["file"] for f in leaks} | set(G.env_secrets_in(self, wt, allowed)))
            if secrets:  # never committed, so never in history; the developer must rewrite
                self.git("reset", "-q", "--", *secrets, cwd=wt)
                self._revert(wt, secrets, snap_before)
        if self.git("diff", "--cached", "--name-only", cwd=wt).strip():
            self.verify_worktree(s)
            msg = f"{s['task']['id']}: {s['task']['title']} (round {s.get('rounds', 0) + 1})"
            self.git("commit", "-q", "-m", msg, cwd=wt)  # hooks (e.g. secret scanners) run
        return {"refused": sorted(refused), "secrets": secrets}

    def cleanup(self) -> list[str]:
        """Remove worktrees of finished tasks (done / dropped). Branches and packets stay."""
        out = []
        for t in self.load_backlog():
            path = self.home / "worktrees" / t["id"]
            if t["status"] in ("done", "dropped") and path.exists():
                try:
                    self.git("worktree", "remove", str(path))
                    out.append(f"{t['id']}: removed {path.relative_to(self.root)}")
                except RuntimeError as e:
                    out.append(f"{t['id']}: kept, {e}")
        self.git("worktree", "prune")
        return out

    def run_checks(self, s: TaskState) -> list[dict]:
        """All setup + checks are required. A failed setup step stops the rest."""
        dev = self.cfg["developers"][s["assignee"]]
        steps = [("setup", c) for c in dev.get("setup", [])]
        steps += [("check", c) for c in dev.get("checks", [])]
        out = []
        for kind, cmd in steps:
            r = self.run_proc(cmd, cwd=s["worktree"], timeout=1800)
            output = self.redact(r["output"][-4000:])
            c = {"cmd": cmd, "kind": kind, "ok": r["ok"], "output": output}
            if r.get("timed_out"):
                c["timed_out"] = True
                c["report"] = (
                    "TIMEOUT: stopped after 30 min (not a failure of the check itself).\n"
                    + c["output"][-1500:]
                )
            out.append(c)
            if kind == "setup" and not r["ok"]:
                break
        return out

    def write_packet(self, s: TaskState, status: str) -> Path:
        """The PR description: what changed, how to try it, criteria, checks, gates."""
        tid, v, g = s["task"]["id"], s.get("verdict", {}), s.get("gate", {})
        path = self.home / "packets" / f"{tid}.md"
        path.parent.mkdir(exist_ok=True)
        base = f"{self.remote_name}/{self.cfg['base_branch']}"
        stat = self.git("diff", "--stat", f"{base}...{s['branch']}")  # no worktree needed
        crit = "\n".join(
            f"- [{'x' if c.get('met') else ' '}] {c['criterion']}"
            + (f" — {c['note']}" if c.get("note") else "")
            for c in v.get("criteria", [])
        )
        checks = "\n".join(
            f"- {'ok' if c['ok'] else 'FAIL'}: `{c['cmd']}`" for c in v.get("checks", [])
        )
        flaky = "\n".join(f"- {t}" for c in v.get("checks", []) for t in c.get("flaky", []))
        gate_lines = "\n".join(f"- {line}" for line in g.get("lines", []))
        if g.get("reason"):
            gate_lines += f"\n- **stopped:** {g['reason']}"
        shots = "\n".join(  # local files: a PR body can't embed them without an upload
            f"- `{Path(p).relative_to(self.root)}`" for p in v.get("screenshots", [])
        )
        shots += "".join(f"\n- **failed:** {x}" for x in v.get("screenshot_problems", []))
        stages, k, parts = s.get("stages") or [], s.get("stage", 0), ""
        if len(stages) > 1:  # which parts the tester accepted; the criteria below are part k's
            parts = "## Parts\n\n" + "\n".join(
                f"- [{'x' if i < k or (i == k and v.get('passed')) else ' '}] "
                f"part {i + 1}: {st['title']}"
                for i, st in enumerate(stages)
            ) + "\n\n"  # fmt: skip
        path.write_text(
            self.redact(
                f"# {tid}: {s['task']['title']}\n\n"
                f"**{status}** · developer: {s['assignee']} · branch `{s['branch']}` · "
                f"rounds: {s.get('rounds', 0)} · sent back: {s.get('fixes', 0)}"
                + (f" (part {k + 1}/{len(stages)})" if len(stages) > 1 else "")
                + f" · judge: {'pass' if v.get('judge_passed') else 'fail'}\n\n"
                f"## What changed\n\n{v.get('summary', '')}\n\n"
                f"## How to try it\n\n{v.get('how_to_try', '')}\n\n{parts}"
                f"## Acceptance criteria{f' (part {k + 1})' if parts else ''}\n\n{crit}\n\n"
                f"## Local checks\n\n{checks or '- (none)'}\n\n"
                + (
                    "## Flaky e2e tests (failed, then passed on retry: not blocking)\n\n"
                    f"{flaky}\n\n"
                    if flaky
                    else ""
                )
                + f"## Gates\n\n{gate_lines or '- (not reached)'}\n\n"
                + (
                    f"## Screenshots (dev machine, seen by the tester)\n\n{shots}\n\n"
                    if shots
                    else ""
                )
                + f"## Files\n\n```\n{stat}```\n\n🤖 Automated by devteam — no human review.\n"
            ),
            encoding="utf-8",
            errors="replace",
        )
        return path


def build_graph(team: Team, checkpointer):
    from devteam import analyst as AN  # they import this module
    from devteam import architect as AR

    cfg = team.cfg
    max_fixes = cfg["max_fix_loops"]
    base = cfg["base_branch"]

    # --- per-task subgraph ---------------------------------------------------------------
    def worktree(s: TaskState):
        tid = s["task"]["id"]
        branch, path = team.branch_for(tid), team.home / "worktrees" / tid
        base_ref, theirs = f"{team.remote_name}/{base}", f"{team.remote_name}/{branch}"
        team.fetch_tasks()
        if not path.exists():  # an existing branch (local, else the remote's) is continued
            if team.git("branch", "--list", branch).strip():
                team.git("worktree", "add", str(path), branch)
            elif team.git("branch", "-r", "--list", theirs).strip():
                team.git("worktree", "add", "-b", branch, str(path), theirs)
            else:  # piece 1 of an Analyst split continues the original's work, if it has any
                start = base_ref
                if orig := s["task"].get("continues"):
                    b = team.branch_for(orig)
                    start = next(
                        (r for r in (b, f"{team.remote_name}/{b}") if team.has_ref(r)), base_ref
                    )
                team.git("worktree", "add", "-b", branch, str(path), start)
        out = {"worktree": str(path), "branch": branch, "rounds": 0, "fixes": 0, "tokens": 0}
        out["last_sendback"] = ""
        team.verify_worktree({**s, **out})
        # earlier work starts from today's dev: a merge, never a rebase (no-op on a new branch)
        r = subprocess.run(
            ["git", "merge", "--no-edit", base_ref], cwd=path, capture_output=True, text=True
        )
        if r.returncode:
            subprocess.run(["git", "merge", "--abort"], cwd=path, capture_output=True)
            team.emit("brain", f"{base} conflicts with {branch}: resolved before round 1", task=tid)
        out["head"] = out["start"] = team.git("rev-parse", "HEAD", cwd=path).strip()
        out["snap"] = team.snapshot(str(path))
        team.emit(
            "brain",
            f"worktree {path.relative_to(team.root)} on {branch}",
            task=tid,
            to=s["assignee"],
        )
        return out

    def halt(s: TaskState, reason: str) -> dict:
        """The task ends blocked with this reason (a limit, the owner, a loop); the other
        developers keep going."""
        team.emit(
            s.get("assignee", "brain"),
            f"BLOCKED: {reason[:300]}",
            task=s["task"]["id"],
            to="reviewer",
            status="idle",
        )
        return {"gate": {"merged": False, "blocked": True, "reason": reason, "lines": []}}

    def resolve(s: TaskState, files: list[str]) -> dict:
        """dev conflicts with the branch: a short step that only resolves the markers, instead
        of a full round. The orchestrator concludes the merge; a normal round follows."""
        dev, tid, wt = s["assignee"], s["task"]["id"], s["worktree"]
        team.emit(dev, f"{base} conflicts in {files}: resolving them", task=tid, status="working")
        snap = team.snapshot(wt)
        untracked = "ls-files", "--others", "--exclude-standard", "-z"
        new_before = set(team.git(*untracked, cwd=wt).split("\0"))
        try:
            out = team.agents.develop(team, {**s, "conflicts": files})
        except BaseException:  # a limit, a crash, a stop: the next try merges afresh
            subprocess.run(["git", "merge", "--abort"], cwd=wt, capture_output=True)
            raise
        tokens = s.get("tokens", 0) + out.get("tokens", 0)
        # this step only resolves: anything else it touched is put back
        changed = team.git("diff", "--name-only", "-z", cwd=wt).split("\0")
        if others := sorted({p for p in changed if p} - set(files)):
            team.git("checkout", "--", *others, cwd=wt)
        for p in set(team.git(*untracked, cwd=wt).split("\0")) - new_before - set(files):
            if p:
                (Path(wt) / p).unlink(missing_ok=True)
        if team.snapshot(wt) != snap:
            subprocess.run(["git", "merge", "--abort"], cwd=wt, capture_output=True)
            raise Violation("the resolve step changed protected files; check them, then resume")
        left = [
            f
            for f in files
            if (Path(wt) / f).is_file() and CONFLICT.search((Path(wt) / f).read_bytes())
        ]
        merging = subprocess.run(
            ["git", "rev-parse", "-q", "--verify", "MERGE_HEAD"], cwd=wt, capture_output=True
        )
        if merging.returncode:
            raise Violation(f"the merge of {base} is no longer in progress after the resolve step")
        secrets = []
        if not left:
            team.git("add", "-A", "--", *files, cwd=wt)
            secrets = [f["file"] for f in G.gitleaks(team, wt, "--pre-commit", "--staged")]
            secrets += G.env_secrets_in(team, wt, files)
        if left or secrets:
            subprocess.run(["git", "merge", "--abort"], cwd=wt, capture_output=True)
            why = f"markers left in {left}" if left else f"secret scan found {sorted(secrets)}"
            return {
                **halt(
                    s,
                    f"could not resolve conflicts with {base} in {files} ({why}). Resolve them "
                    f"by hand on {s['branch']}, then `devteam resume {tid}`",
                ),
                "tokens": tokens,
            }
        team.verify_worktree(s)
        team.git("commit", "-q", "--no-edit", cwd=wt)  # concludes the merge
        team.emit(dev, f"conflicts with {base} resolved in {files}", task=tid, status="idle")
        return {
            "session": out["session"],
            "head": team.git("rev-parse", "HEAD", cwd=wt).strip(),
            "snap": team.snapshot(wt),
            "tokens": tokens,
            "after_dev": "develop",
        }

    def develop(s: TaskState):
        dev, tid, wt = s["assignee"], s["task"]["id"], s["worktree"]
        team.verify_worktree(s)
        if why := SV.task_over(s.get("tokens", 0), SV.limits(cfg, s["task"])):
            return halt(s, why + limit_hint(tid))
        (team.home / "scratch" / tid).mkdir(parents=True, exist_ok=True)  # see the brief
        # today's dev first, by the orchestrator (never the agent); conflicts: a resolve step.
        # Only on a clean worktree at the accepted head: after a crash the round's leftovers
        # (even commits the agent made) are checked by commit_round first, dev comes next round.
        clean = not team.git("status", "--porcelain", "--untracked-files=no", cwd=wt).strip()
        head = team.git("rev-parse", "HEAD", cwd=wt).strip()
        if clean and head != s["head"] and team.dev_merge(wt, s["head"]):
            s = {**s, "head": head, "snap": team.snapshot(wt)}  # our merge, then a crash
        if clean and head == s["head"]:
            if files := team.merge_base(wt):
                return resolve(s, files)
            if (head := team.git("rev-parse", "HEAD", cwd=wt).strip()) != s["head"]:
                s = {**s, "head": head, "snap": team.snapshot(wt)}  # dev's merge, accepted
        what = "fixing: " + s["feedback"][:200] if s.get("feedback") else "building the plan"
        if not s.get("feedback") and len(stages := s.get("stages") or []) > 1:
            k = s.get("stage", 0)
            what = f"building part {k + 1}/{len(stages)}: {stages[k]['title']}"
        team.emit(dev, what, task=tid, status="working")
        if not team.fake:  # e.g. pnpm install: the Codex sandbox has no network
            for cmd in team.cfg["developers"][dev].get("setup", []):
                r = team.run_proc(cmd, cwd=wt, timeout=1800, agent=dev)
                if not r["ok"]:
                    raise RuntimeError(f"setup `{cmd}` failed: {team.redact(r['output'][-800:])}")
        data_before = team.data_at_start(s["task"]) if s["task"].get("writable") else {}
        # this round's screenshots only: an older round's PNG is never taken for a new one
        shutil.rmtree(team.home / "packets" / tid, ignore_errors=True)
        try:
            out = team.agents.develop(team, s)
        except TaskHalted as e:
            team.commit_round(s, s["head"], s["snap"])  # what the round made stays on the branch
            reason = str(e) + (limit_hint(tid) if str(e).startswith("supervisor") else "")
            return {**halt(s, reason), "head": team.git("rev-parse", "HEAD", cwd=wt).strip()}
        except (SubscriptionLimit, BudgetExceeded):  # the same round runs again later
            raise
        except Exception:  # a crash: the round's commits are checked like a halted round's
            team.commit_round(s, s["head"], s["snap"])  # (forbidden ones unwound); loud() blocks
            raise
        res = team.commit_round(s, s["head"], s["snap"])
        head = team.git("rev-parse", "HEAD", cwd=wt).strip()
        if tried := [p for p in res["refused"] if SC.is_protected(p, cfg)]:
            return {**refuse(s, "protected", tried, "tried to change"), "head": head}
        if tried := [p for p in res["refused"] if SC.foreign(p, cfg, dev)]:
            return {**refuse(s, "out_of_scope", tried, "tried to change"), "head": head}
        data_after = team.data_snapshot(s["task"])
        if touched := sorted(p for p, v in data_before.items() if data_after.get(p) != v):
            return {
                **halt(
                    s,
                    f"changed or deleted {len(touched)} existing file(s) under "
                    f"{s['task']['writable']} (e.g. {touched[:5]}); a task may only add new "
                    "files there. Check that data by hand before resuming.",
                ),
                "head": team.git("rev-parse", "HEAD", cwd=wt).strip(),
            }
        summary = out["summary"]
        if res["refused"]:
            shown = res["refused"][:20]
            more = len(res["refused"]) - len(shown)
            summary += (
                "\n\nThe orchestrator reverted your changes to files you may not touch "
                f"(outside your folders, or protected): {', '.join(shown)}"
                + (f" and {more} more" if more else "")
            )
            team.emit(dev, f"touched forbidden files, reverted: {where(res['refused'])}", task=tid)
        if res["secrets"]:
            summary += (
                "\n\nThe secret scan found credentials in these files; they were NOT "
                f"committed and were reverted: {', '.join(res['secrets'])}. Never put "
                "keys or tokens in code; read them from the environment."
            )
            team.emit(dev, f"secret scan: refused {res['secrets']}", task=tid)
        team.emit(dev, summary[:300], task=tid, to="tester", status="idle")
        return {
            "session": out["session"],
            "dev_summary": summary,
            "rounds": s.get("rounds", 0) + 1,
            "head": team.git("rev-parse", "HEAD", cwd=wt).strip(),
            "snap": team.snapshot(wt),
            "tokens": s.get("tokens", 0) + out.get("tokens", 0),
            "after_dev": "test",
        }

    def after_develop(s: TaskState):
        if s.get("gate", {}).get("blocked"):
            return "finish"
        return s.get("after_dev") or "test"

    def refuse(s: TaskState, kind: str, paths: list[str], verb: str = "changed") -> dict:
        """A scope or protected-path violation: logged for the owner, the task ends, no retry."""
        dev, tid = s["assignee"], s["task"]["id"]
        team.violation(tid, dev, kind, f"{verb} {paths}")
        if kind == "out_of_scope":
            return halt(
                s,
                "needs a split before it can be planned: out_of_scope: "
                f"{dev} may only change {list(SC.allowed(cfg, dev, s['task']))}, it {verb} "
                f"{paths[:10]}",
            )
        return halt(s, f"protected: {dev} {verb} files no agent may change: {paths[:10]}")

    def test(s: TaskState):
        tid, dev, wt = s["task"]["id"], s["assignee"], s["worktree"]
        team.emit(
            "tester", "scope gate, then checks on a clean checkout", task=tid, status="working"
        )
        # 1. scope gate on what the branch really changes (never on what the developer says)
        team.fetch(base)
        span = f"{team.remote_name}/{base}...HEAD"
        paths = [p for p in team.git("diff", "--name-only", "-z", span, cwd=wt).split("\0") if p]
        bad = SC.check_paths(paths, cfg, dev, s["task"])
        if bad["protected"]:
            return refuse(s, "protected", bad["protected"])
        if bad["out_of_scope"]:
            return refuse(s, "out_of_scope", bad["out_of_scope"])
        rewrite_ok = SC.rewrite_allowed(cfg, tid)  # owner-approved test rewrites (config)
        findings = SC.bypass(team.git("diff", "-U0", span, cwd=wt), paths, rewrite_ok)
        # After a send-back: what this round did to the branch's own test files. Base -> HEAD
        # cannot show it (UX1b round 2 swapped its new e2e's fixed date for one computed in the
        # browser: all additions vs dev). ponytail: dev merged in between rounds shows here too
        # when it touched the same test files; rare, and the judge sees the diff.
        head = team.git("rev-parse", "HEAD", cwd=wt).strip()
        prev, test_changes, weakened = s.get("tested_head"), "", []
        if prev and prev != head and (tests := [p for p in paths if SC.TEST_FILE.search(p)]):
            test_changes = team.git("diff", f"{prev}..HEAD", "--", *tests, cwd=wt)
            round_diff = team.git("diff", "-U0", f"{prev}..HEAD", "--", *tests, cwd=wt)
            weakened = [f"since the last round: {f}" for f in SC.bypass(round_diff, [], rewrite_ok)]
            findings += weakened
        checks, shots, problems, dev_shots, e2e_failed = [], [], [], [], False
        if findings:  # no checks or judge: the change itself is refused
            team.violation(tid, dev, "bypass", "; ".join(findings))
            verdict = {
                "passed": False,
                "criteria": [],
                "summary": "",
                "how_to_try": "",
                "feedback": "The scope gate refused this change (anti-bypass checks):\n- "
                + "\n- ".join(findings)
                + "\nDo the work in the code itself; never skip, focus or weaken tests.",
            }
        else:
            if not team.fake:  # every check again, from a clean checkout of the committed HEAD
                with team.clean_checkout(s) as clean:
                    cs = {**s, "worktree": clean}
                    # e2e first, run by Kerege outside the developer's sandbox: when it fails,
                    # the round goes back with the failures and no LLM tester call
                    e2e = e2e_gate(cs, clean, paths)
                    e2e_failed = any(not c["ok"] for c in e2e)
                    checks = team.run_checks(cs) + e2e
                    for c in checks:
                        mark = "ok  " if c["ok"] else "TIMEOUT" if c.get("timed_out") else "FAIL"
                        team.emit("tester", f"{mark} {c['cmd']}", task=tid)
                    if not e2e_failed:
                        runs, problems = tester_benchmark(cs, clean)
                        shots, more = G.screenshots(team, cs, s.get("routes") or [], runs=runs)
                        routes = s.get("routes") or []
                        dev_shots, dev_problems = G.dev_screenshots(team, cs, routes)
                        problems += more + dev_problems
                if shots or problems:
                    team.emit(
                        "tester", f"screenshots: {len(shots)} ok, {len(problems)} failed", task=tid
                    )
            if e2e_failed:
                team.emit("tester", "e2e failed: sent back without the LLM tester", task=tid)
                verdict = {
                    "passed": False,
                    "criteria": [],
                    "summary": "",
                    "how_to_try": "",
                    "feedback": (
                        "Kerege ran the e2e tests (Playwright) right after your round and they "
                        "did not finish within the time limit (TIMEOUT below: not a test "
                        "failure; fix any failed tests listed, and do not add slow tests), so "
                        "the tester did not review this round."
                        if any(c.get("timed_out") for c in e2e)
                        else "Kerege ran the e2e tests (Playwright) right after your round and "
                        "they fail, so the tester did not review this round. Fix them first."
                    ),
                }
            else:
                verdict = team.agents.judge(
                    team,
                    {
                        **s,
                        "screenshots": shots + dev_shots,
                        "screenshot_problems": problems,
                        "test_changes": test_changes,
                    },
                    checks,
                )
        verdict["bypass"] = findings
        verdict["screenshots"], verdict["screenshot_problems"] = shots + dev_shots, problems
        failed = [c for c in checks if not c["ok"]]
        verdict["judge_passed"] = verdict["passed"]
        # a visual criterion passes only on the tester's own screenshots, one per route
        no_shots = not e2e_failed and len(shots) < G.expected_shots(cfg, s)
        verdict["passed"] = verdict["passed"] and not failed and not no_shots
        if no_shots:
            verdict["feedback"] = (
                f"{verdict.get('feedback', '')}\n\nTester screenshots missing for "
                f"{s['routes']}: visual criteria cannot pass without them.\n" + "\n".join(problems)
            )
        if failed:
            logs = "\n".join(
                f"$ {c['cmd']}\n{c.get('report') or c['output'][-1500:]}" for c in failed
            )
            verdict["feedback"] = f"{verdict.get('feedback', '')}\n\nFailing checks:\n{logs}"
        verdict["checks"] = checks
        snap = team.snapshot(s["worktree"])  # files the checks made are not the developer's
        if verdict.get("tester_error"):
            team.tester_error(tid, verdict["tester_error"])
        # only the tester's fault when nothing else failed: then not counted as a send-back
        tester_error = bool(verdict.get("tester_error")) and not failed and not no_shots
        fixes = s.get("fixes", 0) + (0 if verdict["passed"] or tester_error else 1)
        # the reason, comparable between rounds: the judge's wording changes, these do not
        key = (
            ""
            if verdict["passed"]
            else "tester error"  # twice in a row ends the task: tester errors never loop
            if tester_error
            else "\n".join(
                sorted(criterion_key(c, s["criteria"]) for c in verdict["criteria"] if not c["met"])
                + sorted(f"check: {c['cmd']}" for c in failed)
                + sorted(f"bypass: {f}" for f in findings)
                + (["screenshots missing"] if no_shots else [])
            )
            or "sent back"
        )
        if key and key == s.get("last_sendback"):
            out = halt(
                s,
                "the tester sent it back twice in a row for the same reason, so no new round "
                f"was started: {verdict['feedback'].strip()[:400]}",
            )
            return {**out, "verdict": verdict, "fixes": fixes, "snap": snap, "last_sendback": key}
        stages, nxt = s.get("stages") or [], s.get("stage", 0) + 1
        if verdict["passed"] and nxt < len(stages):
            team.accept_parts(s["task"], stages[:nxt], head)
            # a part accepted: the next one starts in a fresh round (no feedback, so a new
            # session with the full brief); send-backs and loop detection start again with it
            team.emit(
                "tester",
                f"part {nxt}/{len(stages)} accepted; part {nxt + 1} next: {stages[nxt]['title']}",
                task=tid,
                to=s["assignee"],
                status="idle",
            )
            return {
                **stage_state(s.get("overview", ""), stages, nxt),
                "verdict": verdict,
                "fixes": 0,
                "snap": snap,
                "gate": {},
                "last_sendback": "",
                "feedback": "",
                "part_done": True,
                "part_base": head,  # the next part's judged diff starts here
                "tested_head": head,
            }
        if verdict["passed"]:
            team.emit(
                "tester", "local checks and criteria pass", task=tid, to="gates", status="idle"
            )
        elif tester_error:
            pass  # said by team.tester_error; the developer gets the verdict's feedback
        elif fixes <= max_fixes:
            team.emit(
                "tester",
                f"sent back ({fixes}/{max_fixes}): {verdict['feedback'].strip()[:300]}",
                task=tid,
                to=s["assignee"],
                status="idle",
            )
        else:
            team.emit("tester", "out of fix loops", task=tid, status="idle")
        out = {
            "verdict": verdict,
            "fixes": fixes,
            "snap": snap,
            "gate": {},
            "last_sendback": key,
            "part_done": False,
            # weakened: the next round is compared with the same tests again, so resubmitting
            # them unchanged is the same reason twice in a row (blocked), never a pass
            "tested_head": (
                prev if weakened or (test_changes and verdict.get("tests_weakened")) else head
            ),
        }
        if not verdict["passed"]:
            out["feedback"] = verdict["feedback"]
            out["gate"] = {"reason": "tester: " + verdict["feedback"].strip()[:500]}
        return out

    def e2e_gate(cs: TaskState, clean: str, paths: list[str]) -> list[dict]:
        """Playwright e2e for a change under gates.e2e.when_paths (web/): Kerege runs it first
        after the round, on the clean checkout with a fresh server on a free port, killed with
        the step after. A Codex sandbox cannot open a port (listen EPERM): a developer never
        runs it (A5-fix), and the sandbox stays as it is."""
        e = cfg["gates"].get("e2e")
        if not e or not any(p.startswith(tuple(e["when_paths"])) for p in paths):
            return []
        cmd = e["command"].format(
            port=procs.free_port(), data=team.root / "data", runs_dir=team.runs_dir
        )
        team.emit("tester", "e2e (Playwright) on the clean checkout", task=cs["task"]["id"])
        r = team.run_proc(cmd, cwd=clean, timeout=e["timeout_min"] * 60)
        c = {"cmd": cmd, "kind": "e2e", "ok": r["ok"], "output": e2e_output(r["output"])}
        if flaky := e2e_flaky(r["output"]):  # never hidden: packet, PR and daily report
            c["flaky"] = flaky
            c["output"] = (
                "FLAKY, not blocking (failed, then passed on retry; owner 2026-10-27):\n"
                + "\n".join(flaky)
                + "\n\n"
                + c["output"]
            )
            team.flaky(cs["task"]["id"], flaky)
        if not r["ok"]:
            c["timed_out"] = bool(r.get("timed_out"))
            report = e2e_report(r["output"], c["timed_out"], e["timeout_min"], Path(clean) / "web")
            c["report"] = team.redact(report)
        return [c]

    def tester_benchmark(cs: TaskState, clean: str) -> tuple[Path | None, list[str]]:
        """Pipeline tasks with routes: the tester runs the quick benchmark itself on the clean
        checkout and screenshots that, not the developer's own output."""
        sc = cfg["developers"][cs["assignee"]].get("screenshots") or {}
        if not cs.get("routes") or "{runs}" not in sc.get("serve", ""):
            return None, []
        if team.writes_runs(cs["task"]):  # the runs it made are the ones to look at
            return team.runs_dir, []
        b = cfg["gates"]["benchmark"]
        out = team.home / "bench-tester" / cs["task"]["id"]
        shutil.rmtree(out, ignore_errors=True)
        quick = ",".join(cs["task"].get("quick_only") or b["quick_only"])
        cmd = b["command"].format(data=team.root / "data" / "samples", out=out)
        team.emit(
            "tester", f"quick benchmark on the clean checkout ({quick})", task=cs["task"]["id"]
        )
        r = team.run_proc(f"{cmd} --only {quick}", cwd=clean, timeout=b["quick_min"] * 60 * 4)
        if not r["ok"]:
            return out, [f"the tester's quick benchmark failed: {team.redact(r['output'][-300:])}"]
        return out, []

    def after_test(s: TaskState):
        if s.get("gate", {}).get("blocked"):
            return "finish"
        if s["verdict"]["passed"]:
            return "develop" if s.get("part_done") else "gates"  # the next part, or the gates
        return "develop" if s["fixes"] <= max_fixes else "finish"

    def gates(s: TaskState):
        tid, wt, g = s["task"]["id"], s["worktree"], cfg["gates"]
        lines: list[str] = []
        team.emit(
            "tester", "gates: merge dev, secrets, benchmark, PR, CI", task=tid, status="working"
        )

        def stop(reason: str, *, block: bool = False, head: str | None = None, bench=False):
            fixes = s["fixes"] + (0 if block else 1)
            blocked = block or fixes > max_fixes
            team.emit(
                "tester",
                f"{'BLOCKED' if blocked else 'sent back'}: {reason[:300]}",
                task=tid,
                to="reviewer" if blocked else s["assignee"],
                status="idle",
            )
            out = {
                "gate": {"merged": False, "blocked": blocked, "reason": reason, "lines": lines},
                "fixes": fixes,
                "feedback": reason,
            }
            if head:
                out["head"] = head
            if bench:  # the next gate checks what the developer changed since
                out["bench_sendback"] = head
            return out

        done = team.gh.find_pr(s["branch"], base)
        ours = team.git("rev-list", f"{s.get('start', 'HEAD')}..HEAD", cwd=wt).split()
        if done and done["state"] == "MERGED" and not ours:
            # the brain guard should have kept this task out (P0/A2a on 2026-10-16)
            return stop(
                f"nothing to merge, already done: {s['branch']} was merged in "
                f"{team.gh.url(done)} and this run added no commits",
                block=True,
            )
        if done and done["state"] == "MERGED" and done.get("headRefOid") in ours:
            # resume after a crash: this run's own PR is already merged
            return {
                "gate": {
                    "merged": True,
                    "pr": team.gh.url(done),
                    "number": done["number"],
                    "lines": ["already merged"],
                }
            }
        team.verify_worktree(s)
        team.fetch(base)
        base_ref = f"{team.remote_name}/{base}"
        base_sha = team.git("rev-parse", base_ref).strip()
        # 1. Bring the branch up to date with dev: a merge, never a rebase.
        r = subprocess.run(
            ["git", "merge", "--no-edit", base_ref], cwd=wt, capture_output=True, text=True
        )
        if r.returncode:
            files = team.git("diff", "--name-only", "--diff-filter=U", cwd=wt).split()
            subprocess.run(["git", "merge", "--abort"], cwd=wt, capture_output=True)
            # dev moved since the round began: the next round starts with a resolve step
            return stop(f"conflicts with {base} in {files}; resolve the markers")
        head = team.git("rev-parse", "HEAD", cwd=wt).strip()
        lines.append(f"up to date with `{base}` @ {base_sha[:8]}")
        # 2. Nothing with a secret is ever pushed (history can't be rewritten afterwards).
        leaks = G.gitleaks(team, wt, f"--log-opts={base_ref}..HEAD")
        if leaks:
            return stop(f"secret scan found {leaks}; not pushed", block=True, head=head)
        lines.append("secret scan (gitleaks): clean")
        # 3. Benchmark, when the change can affect it.
        bcfg = g["benchmark"]
        if prev := s.get("bench_sendback"):
            diff = team.git("diff", "-U0", prev, "HEAD", "--", *bcfg["when_paths"], cwd=wt)
            if G.cosmetic_only(diff):
                warn = (
                    "WARNING: since the benchmark sent this task back, "
                    f"{', '.join(bcfg['when_paths'])} changed only in comments or whitespace (or "
                    "not at all): a pass now is another roll of run-to-run noise, not a fix"
                )
                lines.append(warn)
                team.emit("tester", warn, task=tid)
        changed = team.git("diff", "--name-only", f"{base_ref}...HEAD", cwd=wt).split("\n")
        if any(p.startswith(tuple(bcfg["when_paths"])) for p in changed if p):
            team.emit("tester", "benchmark: dev baseline vs this branch", task=tid)
            before = G.benchmark(team, base_sha)
            if before is None:  # never "skipped": that let P2a merge unmeasured
                log = team.home / "bench" / G.bench_key(team, base_sha) / "log.txt"
                tail = log.read_text()[-1500:] if log.exists() else "(no log)"
                return stop(
                    f"benchmark could not run on `{base}` @ {base_sha[:8]}, so this change "
                    f"can't be measured; fix dev first:\n{tail}",
                    block=True,
                    head=head,
                )
            after = G.benchmark(team, head)
            if after is None:
                log = team.home / "bench" / G.bench_key(team, head) / "log.txt"
                log = log.read_text()[-1500:]
                return stop(f"benchmark failed to run on this branch:\n{log}", head=head)
            if G.regressions(before, after, bcfg):  # must repeat on a second run to count
                team.emit("tester", "benchmark regression: measuring those videos again", task=tid)
                after, reg, tries = G.recheck(team, head, before, after, bcfg)
                lines += tries
                if reg:
                    why = "benchmark regression vs dev, repeated on a second run:\n- "
                    return stop(why + "\n- ".join(reg), head=head, bench=True)
            skipped = G.not_compared(before, bcfg)
            lines.append(
                "benchmark: no regression"
                + "".join(f"\n  - not compared: {x}" for x in skipped)
                + "\n\n"
                + G.bench_table(after)
            )
        else:
            lines.append("benchmark: not needed (no pipeline changes)")
        # 4. Push, PR into dev with the packet as description, wait for GitHub CI.
        team.verify_worktree(s)
        team.git("push", "-q", team.remote_url, s["branch"])
        body = team.write_packet({**s, "gate": {"lines": lines}}, "Checking: waiting for CI")
        pr = team.gh.open_pr(s["branch"], base, f"{tid}: {s['task']['title']}", body)
        lines.append(f"PR: {team.gh.url(pr)}")
        team.emit("tester", f"waiting for GitHub CI on {team.gh.url(pr)}", task=tid)
        ok, why = team.gh.wait_ci(pr["number"], head, g["ci_timeout_min"] * 60)
        if not ok:
            return stop(f"GitHub CI failed:\n{why}", head=head)
        lines.append("GitHub CI: green")
        # 5. All gates green: merge exactly the tested commit.
        team.gh.merge(pr["number"], head)
        team.emit(
            "tester",
            f"merged into {base}: {team.gh.url(pr)}",
            task=tid,
            to="reviewer",
            status="idle",
        )
        return {
            "gate": {"merged": True, "pr": team.gh.url(pr), "number": pr["number"], "lines": lines},
            "head": head,
        }

    def after_gates(s: TaskState):
        g = s["gate"]
        if g.get("merged") or g.get("blocked"):
            return "finish"
        return "develop"

    def finish(s: TaskState):
        tid, g = s["task"]["id"], s.get("gate", {})
        merged = bool(g.get("merged"))
        status = "done" if merged else "blocked"
        packet = team.write_packet(s, "Merged into dev" if merged else "Blocked")
        fields: dict = {"status": status, "branch": s["branch"]}
        if merged:
            fields.update(pr=g["pr"], done_at=time.strftime("%Y-%m-%d"), blocked_reason=None)
        else:
            fields["blocked_reason"] = (g.get("reason") or "unknown")[:500]
            if g.get("number"):
                team.gh.open_pr(s["branch"], base, f"{tid}: {s['task']['title']}", packet)
        team.update_task(tid, **fields)
        team.push_status(tid, f"backlog: {tid} {status}" + (f" (#{g['number']})" if merged else ""))
        team.control_file("stop", tid).unlink(missing_ok=True)  # applied: a resume starts clean
        if merged:  # blocked keeps them: a resumed task's baseline is still before its first
            (team.home / "data-snap" / f"{tid}.json").unlink(missing_ok=True)  # round, and its
            (team.home / "parts" / f"{tid}.json").unlink(missing_ok=True)  # accepted parts
        if merged and (orig := s["task"].get("split_from")):
            pieces = [t for t in team.load_backlog() if t.get("split_from") == orig]
            if all(t["status"] == "done" for t in pieces):  # the split task is done too
                team.update_task(orig, status="done", done_at=time.strftime("%Y-%m-%d"))
                team.push_status(orig, f"backlog: {orig} done (its pieces are)")
        if merged:
            team.unblock_ready()  # e.g. A4 done -> P3b back to todo
        if merged:  # finished: the worktree is no longer needed (branch and packet stay)
            try:
                team.git("worktree", "remove", "--force", s["worktree"])
            except RuntimeError:
                pass
        team.emit("tester", f"{status}: {packet.relative_to(team.root)}", task=tid, to="reviewer")
        acted = "" if merged else AN.on_blocked(team, tid)  # "" with no switch: Kerege v2
        return {
            "results": [
                {
                    "id": tid,
                    "status": status,
                    "packet": str(packet),
                    "pr": g.get("pr", ""),
                    "reason": "" if merged else fields["blocked_reason"],
                    "analyst": acted,
                }
            ]
        }

    def loud(name, fn):
        """A parallel task's error surfaces only when the other tasks finish (hours): say it
        now, so the owner can `devteam stop`, fix and `resume`."""

        def node(s: TaskState):
            if team.stopping.is_set():
                raise StopRun(f"stopped before {name}")
            tid = s["task"]["id"]
            team._ctx.task = tid  # run_proc in this node checks `devteam stop <task>`
            try:
                if name in ("develop", "test", "gates") and (why := team.stop_reason(tid)):
                    return halt(s, why)
                return procs.clean(fn(s))  # the checkpoint can't store a lone surrogate
            except TaskHalted as e:  # a limit or the owner: this task ends, the others go on
                return halt(s, str(e))
            except (SubscriptionLimit, BudgetExceeded):  # the caller waits or stops, no crash
                raise
            except Exception as e:  # this task ends blocked; raising would stop every task's
                # branch of the run (2026-10-21: UX-dense's tester crash ended the whole run)
                at = traceback.extract_tb(e.__traceback__)[-1]
                reason = (
                    f"CRASHED in {name} at {Path(at.filename).name}:{at.lineno}: {e}. Fix it, "
                    f"then `devteam resume {tid}`"
                )
                # ponytail: a crash in finish itself leaves the backlog in_progress (logged)
                out = {**halt(s, reason), "branch": s.get("branch") or team.branch_for(tid)}
                return procs.clean(out)
            finally:
                team._ctx.task = ""

        return node

    sub = StateGraph(TaskState)
    for name, fn in [
        ("worktree", worktree),
        ("develop", develop),
        ("test", test),
        ("gates", gates),
        ("finish", finish),
    ]:
        sub.add_node(name, loud(name, fn))
    sub.add_edge(START, "worktree")
    sub.add_conditional_edges(
        "worktree",
        lambda s: "finish" if s.get("gate", {}).get("blocked") else "develop",
        ["develop", "finish"],
    )
    sub.add_conditional_edges("develop", after_develop, ["test", "develop", "finish"])
    sub.add_conditional_edges("test", after_test, ["gates", "develop", "finish"])
    sub.add_conditional_edges("gates", after_gates, ["develop", "finish"])
    sub.add_edge("finish", END)

    # --- top level -----------------------------------------------------------------------
    def brain(s: TeamState, config: RunnableConfig):
        """Plans; writes only the owner's stop / resume requests. Tasks this run already
        claimed count as open again."""
        run = config["configurable"]["thread_id"]
        if team.stopping.is_set():
            raise StopRun("stopped before planning")
        devs = {d: cfg["developers"][d] for d in s.get("developers") or cfg["developers"]}
        team.emit("brain", "reading the backlog", status="working")
        team.apply_controls()
        todo = [
            t
            for t in team.load_backlog()
            if t["status"] == "todo" or (t["status"] == "in_progress" and t.get("run") == run)
        ]
        if owner := sorted(t["id"] for t in todo if t.get("owner_only")):  # e.g. P4a-2a-run
            team.emit("brain", f"skipping owner-only tasks, the owner runs them: {owner}")
            todo = [t for t in todo if not t.get("owner_only")]
        # Never redo landed work, even if the backlog lost its status (2026-10-16: P0, A2a).
        if again := sorted(t["id"] for t in todo if t["id"] in team.merged_tasks()):
            team.emit("brain", f"skipping tasks already merged into dev, mark them done: {again}")
            todo = [t for t in todo if t["id"] not in again]
        done = {t["id"] for t in team.load_backlog() if t["status"] == "done"}
        if waiting := sorted(t["id"] for t in todo if not set(t.get("depends_on") or []) <= done):
            team.emit("brain", f"skipping tasks whose depends_on are not all done: {waiting}")
            todo = [t for t in todo if t["id"] not in waiting]
        accepted = {}
        for t in todo:  # merged_tasks() fetched the task branches
            if work := team.prior_work(t["id"]):
                t["previous_work"] = work
            parts, part_base = team.accepted_parts(t)
            if parts:  # kept on resume: only the rest is planned, judged from part_base on
                t["accepted_parts"], t["part_base"] = parts, part_base
                accepted[t["id"]] = parts
            if team.writes_runs(t):  # routes must name runs it makes, never these
                t["runs_before_task"] = team.runs_before_task(t)
        if team.only:  # `devteam run --only P1`
            todo = [t for t in todo if t["id"] in team.only]
        known = {a for d in cfg["developers"].values() for a in d["areas"]}
        if no_area := [t["id"] for t in todo if t.get("area") not in known]:
            team.emit("brain", f"skipping tasks without a known area {sorted(known)}: {no_area}")
        areas = {a for d in devs.values() for a in d["areas"]}  # other developers' tasks wait
        todo = [t for t in todo if t.get("area") in areas]
        if AN.enabled(team, "architect"):  # the next task of each developer (backlog order)
            for d in devs.values():
                if t := next((t for t in todo if t.get("area") in d["areas"]), None):
                    if (text := AR.brief(team, t)) is None:  # it waits on a new prerequisite
                        todo.remove(t)
                    elif text:
                        t["architect_brief"] = text
        assignments, splits, feedback = [], [], None
        open_ids = {t["id"] for t in todo}
        for attempt in range(2):  # one retry with the validation errors
            plans = team.agents.plan(team, todo, feedback=feedback, devs=list(devs)) if todo else []
            splits = [p for p in plans if p.get("split") and p.get("task_id") in open_ids]
            plans = [p for p in plans if not p.get("split")]
            for p in plans:  # more parts than one task holds: the owner splits it
                n = len(p.get("parts") or []) + len(accepted.get(p.get("task_id"), []))
                if n > MAX_PARTS:
                    splits.append(
                        {"task_id": p["task_id"], "split": f"it needs {n} parts (max {MAX_PARTS})"}
                    )
            plans = [p for p in plans if p["task_id"] not in {x["task_id"] for x in splits}]
            try:
                assignments = validate_plan(plans, todo, {**cfg, "developers": devs})
                break
            except PlanError as e:
                if attempt and e.only_split:  # never planned across folders or oversized
                    splits += [
                        {"task_id": t, "split": f"its criteria need other folders: {paths}"}
                        for t, paths in e.outside.items()
                    ] + [
                        {"task_id": t, "split": f"it does not fit in {MAX_PARTS} parts: {why}"}
                        for t, why in e.too_big.items()
                        if t not in e.outside
                    ]
                    drop = set(e.outside) | set(e.too_big)
                    plans = [p for p in plans if p["task_id"] not in drop]
                    assignments = validate_plan(plans, todo, {**cfg, "developers": devs})
                    break
                if attempt:
                    raise
                feedback = str(e)
                team.emit("brain", f"plan rejected, re-planning: {e}")
        team.emit("brain", f"planned {len(assignments)} task(s)", status="idle")
        return {"assignments": assignments, "splits": splits}

    def claim(s: TeamState, config: RunnableConfig):
        """Writes the checkpointed assignments to the backlog. Idempotent."""
        run = config["configurable"]["thread_id"]
        for sp in s.get("splits") or []:  # the owner splits them in the backlog
            reason = (  # the work spans more than one developer's folders, or is too big
                f"needs a split before it can be planned: {sp['split']}. Split it in "
                "backlog.yaml (one task per developer, at most 3 parts each, depends_on between "
                "them)"
            )
            team.update_task(sp["task_id"], status="blocked", blocked_reason=reason[:500])
            team.push_status(sp["task_id"], f"backlog: {sp['task_id']} blocked (needs a split)")
            team.emit("brain", reason, task=sp["task_id"], to="reviewer")
            AN.on_blocked(team, sp["task_id"])
        for a in s["assignments"]:
            team.update_task(a["task"]["id"], status="in_progress", owner=a["assignee"], run=run)
            # on dev too: a claim kept only in the main checkout left backlog.yaml modified there
            team.push_status(a["task"]["id"], f"backlog: {a['task']['id']} in_progress")
            stages = a.get("stages") or []
            n, total = len(stages), sum(len(x["criteria"]) for x in stages)
            team.emit(
                "brain",
                f"{a['task']['title']} — plan with {len(a['criteria'])} criteria"
                + (f" in part 1 ({total} in {n} parts)" if n > 1 else ""),
                task=a["task"]["id"],
                to=a["assignee"],
            )
        return {}

    def fan_out(s: TeamState):
        return [Send("task", a) for a in s["assignments"]] or END

    top = StateGraph(TeamState)
    top.add_node("brain", brain)
    top.add_node("claim", claim)
    top.add_node("task", sub.compile())
    top.add_edge(START, "brain")
    top.add_edge("brain", "claim")
    top.add_conditional_edges("claim", fan_out, ["task", END])
    top.add_edge("task", END)
    return top.compile(checkpointer=checkpointer)
