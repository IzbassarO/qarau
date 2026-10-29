"""Analyst (Kerege v3 K2, owner 2026-10-28): every blocked task is diagnosed, and a few plan
changes are made without the owner, each its own backlog commit that `devteam undo <commit>`
reverts. Night 2026-10-27: all three tasks were blocked by 00:46 and Kerege idled 10 h
(UI-evidence-b needed GET /api/me the backend lacked, COV1 was too big, UI-report hit a tester API
error); nobody diagnosed or re-planned.

Allowed changes (at most MAX_CHANGES_24H a day, Architect's prerequisites included), each checked
by the backlog tests before it is pushed: add a prerequisite task the blocked one waits on; split
a too-big task into 2-3 entries that take every criterion of the original exactly once (the
original keeps its goal, gets status `split`, and is done when its pieces are); resume once with a
hint; resume once after an environment issue. Never: change an entry's goal or criteria, touch
done or LATER tasks or any branch. Kerege errors and unclear cases go to the owner:
.state/analyst/<task>.md with a ready Claude Code prompt, and a notification.
"""

import json
import time
from pathlib import Path

import yaml

from devteam import watchdog
from devteam.graph import (
    DEPS_WAIT,
    OWNER_STOP,
    done_when,
    outside_paths,
    owner_additions,
    waits_on_dependencies,
)

DIAGNOSES = [
    "missing_dependency",
    "too_big",
    "developer_failure",
    "environment",
    "tester_or_kerege_error",
    "unclear",
]
CHANGES = ["add_prerequisite", "split", "resume_with_hint", "resume_after_environment"]
MAX_CHANGES_24H = 3
MAX_PIECES = 3
RULES = """Rules: new git worktree off origin/dev (a feature branch); NEVER switch the main checkout
(~/Documents/Startup/qarau stays on dev); read devteam/.state/* only; do not edit any
devteam/<task> branch; never merge; no force-push, no history rewrite, do not bypass the pre-push
hook. No one-off scripts committed; test behaviour, not the wording of prompts, backlog or docs.
Before every push run every CI command the change touches (.github/workflows/ci.yml) and
`uv run devteam rehearse`. Open a PR into dev, do not merge. Report in Russian, fact vs opinion."""

SYSTEM = """You are the Analyst of Kerege, the automated dev team of Qarau (no human reviews
code). A task has just ended blocked. Find out why from the evidence, then choose what to do.

diagnosis, exactly one:
- missing_dependency: the task needs something another developer's area lacks (an API route,
  a field, a pipeline output); the developer cannot build it (scope gate) and keeps failing.
- too_big: the task cannot fit (token or time limits of the supervisor, too many criteria or
  parts, "needs a split").
- developer_failure: the developer did not meet criteria it could meet (wrong approach, tests,
  repeated send-backs for its own code).
- environment: the machine or tools failed (a CLI vanished, network, disk, a port, a service
  down), not the code.
- tester_or_kerege_error: the tester's API call failed, the orchestrator crashed, a gate or
  screenshot step broke: a bug in Kerege, not in the task.
- unclear: the evidence does not say.

action, exactly one:
- add_prerequisite (missing_dependency): a new task in the area that lacks the piece (its
  developer owns those folders), with a goal in the backlog's style: "Today ...", "Build
  (<folders> only): ...", "Not in this task: ...", "Done when: <checkable clauses separated by
  ;>". The blocked task waits on it, then runs again unchanged.
- split (too_big): 2-3 pieces, run in order; each piece names the numbers of the original
  <criteria> it takes; every number exactly once across the pieces. `intro` says what the piece
  builds ("Today ...", "Build ..."), without "Done when" (Kerege writes it from the numbers).
  Accepted parts stay on the original's branch, which piece 1 continues: give piece 1 the
  criteria they cover.
- resume_with_hint (developer_failure, once per task): a concrete hint for the next attempt
  (file, cause, fix), not a restatement of the criteria.
- resume_after_environment (environment, once per task): run it again unchanged.
- report: the owner must act (tester_or_kerege_error, unclear, or anything the other actions
  cannot fix). `prompt` is a ready prompt for a Claude Code session that fixes the cause: the
  context (task, what failed, the evidence with file:line), what to change, how to verify.

Never propose changing an existing task's goal or criteria. Fields that do not belong to the
chosen action are empty ("" or [] or empty strings). `why`: two or three sentences with the
evidence."""

SCHEMA = {
    "type": "object",
    "properties": {
        "diagnosis": {"type": "string", "enum": DIAGNOSES},
        "why": {"type": "string"},
        "action": {"type": "string", "enum": [*CHANGES, "report"]},
        "prerequisite": {
            "type": "object",
            "properties": {k: {"type": "string"} for k in ("title", "area", "goal")},
            "required": ["title", "area", "goal"],
            "additionalProperties": False,
        },
        "pieces": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "intro": {"type": "string"},
                    "criteria": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["title", "intro", "criteria"],
                "additionalProperties": False,
            },
        },
        "hint": {"type": "string"},
        "prompt": {"type": "string"},
    },
    "required": ["diagnosis", "why", "action", "prerequisite", "pieces", "hint", "prompt"],
    "additionalProperties": False,
}

REVIEW_SYSTEM = """You are the Analyst of Kerege. Look at the open backlog (task graph, blocked
tasks and why) and the plan changes made today. Propose adjustments for the owner: re-orderings,
splits, missing prerequisites, tasks to drop or to park. Proposals only: nothing is changed.
Each proposal: one or two sentences, the task ids, why. None needed: an empty list."""

REVIEW_SCHEMA = {
    "type": "object",
    "properties": {"proposals": {"type": "array", "items": {"type": "string"}}},
    "required": ["proposals"],
    "additionalProperties": False,
}


class Refused(ValueError):
    """A plan change that breaks a rule: it goes to the owner instead."""


def enabled(team, role: str) -> bool:
    return bool((team.cfg.get("models") or {}).get(role, {}).get("enabled"))


def folder(team) -> Path:
    (d := team.home / "analyst").mkdir(exist_ok=True)
    return d


def actions(team, since_s: float = 0) -> list[dict]:
    """The plan-change log (.state/analyst/actions.jsonl), oldest first."""
    try:
        lines = (folder(team) / "actions.jsonl").read_text().splitlines()
    except FileNotFoundError:
        return []
    rows = [json.loads(x) for x in lines if x.strip()]
    return [r for r in rows if r.get("epoch", 0) >= since_s]


def _log(team, **row):
    row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "epoch": time.time(), **row}
    with open(folder(team) / "actions.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


def changes_left(team) -> int:
    made = [r for r in actions(team, time.time() - 24 * 3600) if r.get("commit")]
    return MAX_CHANGES_24H - len(made)


def area_dev(cfg: dict, area: str) -> str:
    return next((d for d, c in cfg["developers"].items() if area in c["areas"]), "")


def _entry(data: dict, tid: str) -> dict:
    t = next((t for t in data["tasks"] if t["id"] == tid), None)
    if t is None:
        raise Refused(f"{tid} is not in the backlog")
    return t


def _fresh_id(team, base: str) -> str:
    ids = {t["id"] for t in team.load_backlog()}
    return next(x for x in (base, *(f"{base}{n}" for n in range(2, 99))) if x not in ids)


def _commit(team, tid: str, by: str, action: str, why: str, edit) -> str:
    """One plan change: under the daily cap, its own backlog commit, logged."""
    if changes_left(team) <= 0:
        raise Refused(f"{MAX_CHANGES_24H} plan changes were made in the last 24 h (the cap)")
    msg = f"backlog: {by}: {action} {tid}: {' '.join(why.split())}"[:300]
    sha = team.change_backlog(edit, msg, tid)
    if sha is None:
        raise Refused("the backlog tests refused the change (see the events log)")
    _log(team, task=tid, by=by, action=action, why=why, commit=sha)
    team.emit(by, f"{action}: {why[:200]} (undo: `devteam undo {sha[:9]}`)", task=tid)
    return sha


def add_prerequisite(team, tid: str, pre: dict, why: str, by: str = "analyst") -> str:
    """A new task in another area that `tid` waits on: inserted before it; `tid` gets it in
    depends_on (a blocked one "waits for" it, so it goes back to todo when it is done)."""
    area, goal = pre.get("area", ""), (pre.get("goal") or "").strip()
    dev = area_dev(team.cfg, area)
    if not dev:
        raise Refused(f"prerequisite area {area!r} belongs to no developer")
    if not done_when(goal) or not pre.get("title"):
        raise Refused("the prerequisite needs a title and a goal with checkable 'Done when:'")
    new = {"id": _fresh_id(team, f"{tid}-pre"), "area": area, "title": pre["title"], "goal": goal}
    if outside := outside_paths([goal], dev, team.cfg, new):
        raise Refused(f"the prerequisite's goal names folders {dev} does not own: {outside}")
    new |= {"status": "todo", "added_by": by, "reason": " ".join(why.split())}

    def edit(data: dict, _raw: str):
        t = _entry(data, tid)
        if t["status"] in ("done", "later", "dropped", "split"):
            raise Refused(f"{tid} is {t['status']}")
        if any(x["id"] == new["id"] for x in data["tasks"]):
            return  # already there (made on dev before, now in this file)
        data["tasks"].insert(data["tasks"].index(t), dict(new))
        t["depends_on"] = [*(t.get("depends_on") or []), new["id"]]
        if t["status"] == "blocked":
            t["blocked_reason"] = f"{DEPS_WAIT} {new['id']} (added by the {by}: {why})"[:500]

    _commit(team, tid, by, "add_prerequisite", f"waits for new {new['id']}: {why}", edit)
    return new["id"]


def criteria(goal: str) -> list[str]:
    """The original's criteria, numbered 1..N in the Analyst's prompt: "Done when:" clauses,
    then Owner additions."""
    return done_when(goal) + owner_additions(goal)


def backlog_problems(tasks: list[dict], cfg: dict) -> list[str]:
    """Entries Kerege added are whole (a reason, checkable criteria, their developer's folders,
    nothing LATER), and every split task's criteria are in its pieces exactly once. Run on every
    backlog push (gates.backlog_check)."""
    by, out = {t["id"]: t for t in tasks}, []
    later = {t["id"] for t in tasks if t["status"] == "later"}
    for t in tasks:
        tid = t["id"]
        if by_ := t.get("added_by"):
            dev = area_dev(cfg, t.get("area", ""))
            if by_ not in ("analyst", "architect") or not (t.get("reason") or t.get("split_from")):
                out.append(f"{tid}: added_by analyst / architect with a reason or split_from")
            if not dev or not done_when(t.get("goal", "")):
                out.append(f"{tid}: an area of a developer and a goal with 'Done when:'")
            elif outside := outside_paths([t["goal"]], dev, cfg, t):
                out.append(f"{tid}: its goal names folders {dev} does not own: {outside}")
            if set(t.get("depends_on") or []) & later:
                out.append(f"{tid}: depends on a LATER task")
        if t["status"] == "split":
            pieces = [by.get(i) or {} for i in t.get("split_into") or []]
            if not pieces or any(p.get("split_from") != tid for p in pieces):
                out.append(f"{tid}: split_into names its pieces, each with split_from: {tid}")
            got = sorted(c for p in pieces for c in criteria(p.get("goal", "")))
            if got != sorted(criteria(t["goal"])):
                out.append(f"{tid}: its pieces do not take each of its criteria exactly once")
    return out


def split(team, tid: str, pieces: list[dict], why: str) -> list[str]:
    """2-3 new entries in order, every criterion of `tid` in exactly one; `tid` keeps its goal,
    becomes `split` (done when its pieces are); piece 1 continues its branch."""
    t = _entry({"tasks": team.load_backlog()}, tid)
    crit, dw = criteria(t["goal"]), done_when(t["goal"])
    nums = sorted(n for p in pieces for n in p.get("criteria") or [])
    if not 2 <= len(pieces) <= MAX_PIECES:
        raise Refused(f"a split has 2-{MAX_PIECES} pieces, not {len(pieces)}")
    if len(crit) < 2 or nums != list(range(1, len(crit) + 1)):
        raise Refused(f"the pieces must take criteria 1..{len(crit)} exactly once, got {nums}")
    if any(not p.get("criteria") for p in pieces):
        raise Refused("a piece without criteria")
    ids = [_fresh_id(team, f"{tid}-s{k}") for k in range(1, len(pieces) + 1)]
    entries = []
    for k, (p, pid) in enumerate(zip(pieces, ids, strict=True)):
        intro = (p.get("intro") or "").strip()
        if "Done when" in intro or "Owner additions" in intro:
            raise Refused("a piece's intro may not write criteria")
        mine = [crit[n - 1] for n in sorted(p["criteria"])]
        others = ", ".join(x for x in ids if x != pid)
        goal = (
            f"{intro}\n\nSplit from {tid} by the Analyst ({time.strftime('%Y-%m-%d')}), piece "
            f"{k + 1} of {len(ids)}: {tid}'s goal stays the spec.\n\nNot in this task: what "
            f"{others} build.\n\nDone when: " + "; ".join(c for c in mine if c in dw) + "."
        )
        if extra := [c for c in mine if c not in dw]:  # Owner additions stay word for word
            goal += f"\n\nOwner additions (from {tid}):\n" + "\n".join(f"- {c}" for c in extra)
        e = {"id": pid, "area": t["area"], "title": p.get("title") or f"{t['title']} ({k + 1})"}
        e |= {"goal": goal + "\n", "status": "todo", "added_by": "analyst", "split_from": tid}
        e["depends_on"] = list(t.get("depends_on") or []) if k == 0 else [ids[k - 1]]
        if k == 0:
            e["continues"] = tid  # accepted parts and earlier work stay on its branch
        entries.append(e)

    def edit(data: dict, _raw: str):
        t = _entry(data, tid)
        if t["status"] == "split":
            return  # already split
        if t["status"] != "blocked":
            raise Refused(f"{tid} is {t['status']}, not blocked")
        at = data["tasks"].index(t) + 1
        data["tasks"][at:at] = [dict(e) for e in entries]
        t.update(status="split", split_into=ids)
        t.pop("blocked_reason", None)

    _commit(team, tid, "analyst", "split", f"into {', '.join(ids)}: {why}", edit)
    kept = team.home / "parts" / f"{tid}.json"
    if kept.exists():  # accepted parts stay accepted: piece 1 plans only the rest
        parts = json.loads(kept.read_text())
        (kept.parent / f"{ids[0]}.json").write_text(
            json.dumps({**parts, "goal": entries[0]["goal"]})
        )
    return ids


def resume(team, tid: str, why: str, hint: str = "") -> None:
    """Back to todo, once per task (with a hint for the brain and the developer)."""
    if any(r["task"] == tid and r["action"].startswith("resume") for r in actions(team)):
        raise Refused(f"the Analyst resumed {tid} once already")

    def edit(data: dict, _raw: str):
        t = _entry(data, tid)
        if t["status"] == "todo":
            return
        if t["status"] != "blocked":
            raise Refused(f"{tid} is {t['status']}, not blocked")
        t["status"] = "todo"
        t.pop("blocked_reason", None)
        if hint:
            t["analyst_hint"] = " ".join(hint.split())

    action = "resume_with_hint" if hint else "resume_after_environment"
    _commit(team, tid, "analyst", action, why, edit)


def evidence(team, t: dict) -> str:
    """What the Analyst reads: the entry, its events, the packet, the task graph."""
    from devteam.dashboard import recent_events  # graph -> analyst at run time only

    tid = t["id"]
    events = [e for e in recent_events(team) if e.get("task") == tid][-40:]
    packet = team.home / "packets" / f"{tid}.md"
    graph = [
        {k: x[k] for k in ("id", "area", "status", "title", "depends_on", "added_by") if k in x}
        for x in team.load_backlog()
        if x["status"] not in ("done", "later", "dropped")
    ]
    parts = team.home / "parts" / f"{tid}.json"
    return "\n\n".join(
        [
            "<developers>\n"
            + yaml.safe_dump(
                {
                    n: {"areas": d["areas"], "owns": d["owns"]}
                    for n, d in team.cfg["developers"].items()
                }
            )  # fmt: skip
            + "</developers>",
            f"<task>\n{yaml.safe_dump(t, allow_unicode=True, sort_keys=False)}</task>",
            "<criteria>\n"
            + "\n".join(f"{i}. {c}" for i, c in enumerate(criteria(t.get("goal", "")), 1))
            + "\n</criteria>",
            "<accepted_parts>\n" + (parts.read_text() if parts.exists() else "none") + "\n"
            "</accepted_parts>",
            "<events>\n"
            + "\n".join(
                f"{e.get('ts')} {e.get('agent')}: {str(e.get('msg'))[:600]}" for e in events
            )
            + "\n</events>",
            "<packet>\n"
            + (packet.read_text()[-8000:] if packet.exists() else "none")
            + "\n</packet>",
            "<open_tasks>\n"
            + yaml.safe_dump(graph, allow_unicode=True, sort_keys=False)
            + "</open_tasks>",
            "<earlier_analyst_actions_on_this_task>\n"
            + "\n".join(json.dumps(r) for r in actions(team) if r["task"] == tid)
            + "\n</earlier_analyst_actions_on_this_task>",
            f"Plan changes left in the last 24 h: {changes_left(team)} of {MAX_CHANGES_24H}.",
        ]
    )


def on_blocked(team, tid: str) -> str:
    """Diagnose a task that just ended blocked and act. Returns the change made ("" when the
    owner has to act: the developer may pause). Never raises: the run goes on."""
    t = next((x for x in team.load_backlog() if x["id"] == tid), None)
    if t is None or not enabled(team, "analyst") or t["status"] != "blocked":
        return ""
    reason = str(t.get("blocked_reason") or "")
    if reason.startswith(OWNER_STOP) or waits_on_dependencies(t):
        return ""  # the owner's own stop; a task parked on its dependencies
    team.emit("analyst", "diagnosing why it is blocked", task=tid, status="working")
    try:
        v = team.agents.ask(team, "analyst", tid, SYSTEM, evidence(team, t), SCHEMA)
    except Exception as e:  # noqa: BLE001 (the run goes on; the owner is told)
        v = {"diagnosis": "unclear", "why": f"the Analyst's call failed: {e}", "action": "report"}
    note = ""
    try:
        if v["action"] == "add_prerequisite" and v["diagnosis"] == "missing_dependency":
            add_prerequisite(team, tid, v.get("prerequisite") or {}, v["why"])
        elif v["action"] == "split" and v["diagnosis"] == "too_big":
            split(team, tid, v.get("pieces") or [], v["why"])
        elif v["action"] == "resume_with_hint" and v["diagnosis"] == "developer_failure":
            if not (v.get("hint") or "").strip():
                raise Refused("resume_with_hint without a hint")
            resume(team, tid, v["why"], v["hint"])
        elif v["action"] == "resume_after_environment" and v["diagnosis"] == "environment":
            resume(team, tid, v["why"])
        elif v["action"] != "report":
            raise Refused(f"{v['action']} does not fit the diagnosis {v['diagnosis']}")
        else:
            raise Refused("")
    except Refused as e:
        note = str(e)
    except Exception as e:  # noqa: BLE001 (a push that failed: the owner decides)
        note = f"the change failed: {e}"
    else:
        team.emit("analyst", f"{v['diagnosis']}: {v['action']}", task=tid, status="idle")
        return v["action"]
    report(team, t, v, note)
    team.emit("analyst", f"{v['diagnosis']}: the owner decides", task=tid, status="idle")
    return ""


def report(team, t: dict, v: dict, note: str = ""):
    """.state/analyst/<task>.md: the diagnosis and a ready Claude Code prompt; a notification."""
    tid = t["id"]
    path = folder(team) / f"{tid}.md"
    prompt = (v.get("prompt") or "").strip() or (
        f"Kerege task {tid} ({t.get('title', '')}) is blocked: {t.get('blocked_reason', '')}\n"
        f"Diagnosis: {v['diagnosis']}. {v.get('why', '')}\nFind the cause, fix it, verify."
    )
    path.write_text(
        f"# {tid}: {v['diagnosis']}\n\n{time.strftime('%Y-%m-%d %H:%M')}\n\n{v.get('why', '')}\n\n"
        f"Blocked: {t.get('blocked_reason', '')}\n\n"
        + (f"Not changed by the Analyst: {note}\n\n" if note else "")
        + f"## Prompt for Claude Code\n\n```\n{prompt}\n\n{RULES}\n```\n"
    )
    _log(team, task=tid, by="analyst", action="report", why=v.get("why", ""), note=note)
    watchdog.notify(team, f"{tid}: {v['diagnosis']}, needs you: devteam/.state/analyst/{tid}.md")


def review(team) -> list[str]:
    """Plan adjustments, proposals only (every 6 h and at the end of a continuous run): kept in
    .state/analyst/proposals-<day>.md for the daily report."""
    if not enabled(team, "analyst"):
        return []
    tasks = [t for t in team.load_backlog() if t["status"] not in ("done", "later", "dropped")]
    today = actions(team, time.time() - 24 * 3600)
    prompt = (
        "<open_tasks>\n" + yaml.safe_dump(tasks, allow_unicode=True, sort_keys=False)
        + "</open_tasks>\n\n<plan_changes_last_24h>\n"
        + "\n".join(json.dumps(r) for r in today)
        + "\n</plan_changes_last_24h>"
    )  # fmt: skip
    try:
        out = team.agents.ask(team, "analyst", "review", REVIEW_SYSTEM, prompt, REVIEW_SCHEMA)
    except Exception as e:  # noqa: BLE001
        team.emit("analyst", f"plan review failed: {e}")
        return []
    proposals = [p for p in out.get("proposals") or [] if p.strip()]
    with open(folder(team) / f"proposals-{time.strftime('%Y-%m-%d')}.md", "a") as f:
        f.write(f"\n### {time.strftime('%H:%M')}\n\n")
        f.write("\n".join(f"- {p}" for p in proposals) or "- none")
        f.write("\n")
    return proposals


def report_lines(team, day: str) -> list[str]:
    """The daily report's "Plan adjustments": changes made that day (with their undo) and the
    Analyst's proposals."""
    made = [r for r in actions(team) if r["ts"].startswith(day)]
    path = folder(team) / f"proposals-{day}.md"
    if not made and not path.exists():
        return []
    lines = ["", "## Plan adjustments", ""]
    for r in made:
        if r.get("commit"):
            lines.append(
                f"- {r['task']}: **{r['action']}** by the {r['by']}: {r['why'][:300]} "
                f"(`devteam undo {r['commit'][:9]}`)"
            )
        else:
            lines.append(
                f"- {r['task']}: needs you, see `.state/analyst/{r['task']}.md`"
                + (f" ({r['note'][:200]})" if r.get("note") else "")
            )
    if path.exists():
        lines += ["", "Proposals (nothing changed):", path.read_text().strip()]
    return lines
