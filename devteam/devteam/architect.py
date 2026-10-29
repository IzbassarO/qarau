"""Architect (Kerege v3 K2, owner 2026-10-28): before the brain plans a task, a read-only context
brief from dev's code: the files the task touches, the interfaces and contracts it builds on, and
what is missing. A missing piece in another developer's area becomes a prerequisite task the task
waits on (2026-10-27: UI-evidence-b needed GET /api/me, which the backend lacked; the web
developer guessed the role from 403 texts and was sent back until it blocked).

The brief is cached per task, goal and dev commit (.state/architect/<task>.json)."""

import hashlib
import json
import re

import yaml

from devteam import analyst as AN

PATH = re.compile(r"\b(?:web|backend|pipeline|infra|docs)/[\w./@\-]*[\w/]")
MAX_FILE, MAX_FILES = 20_000, 12  # characters per file, files read

SYSTEM = """You are the Architect of Kerege, the automated dev team of Qarau. Before the tech lead
plans a task, you write a context brief for the developer from the code on dev (read-only):
- files: the files the task will touch or read, with one line on each;
- interfaces: the functions, components, types and API routes it builds on (exact names);
- contracts: request / response shapes, data formats, roles, i18n keys it must follow;
- missing: what the task needs that does not exist yet.
`brief`: the above as short plain text a senior engineer reads in a minute.
If something missing lies in ANOTHER developer's area (e.g. a web task needs a backend route that
is not in <backend_routes>), propose `prerequisite`: a small task in that area, goal in the
backlog's style ("Today ...", "Build (<folders> only): ...", "Not in this task: ...", "Done when:
<checkable clauses separated by ;>"). Never for something the task's own developer can build, or
that an open task in <open_tasks> already builds. No prerequisite: all three fields empty."""

SCHEMA = {
    "type": "object",
    "properties": {
        "brief": {"type": "string"},
        "missing": {"type": "array", "items": {"type": "string"}},
        "prerequisite": AN.SCHEMA["properties"]["prerequisite"],
    },
    "required": ["brief", "missing", "prerequisite"],
    "additionalProperties": False,
}


def _grep(team, ref: str, pattern: str, *paths: str) -> str:
    try:
        out = team.git(  # Team.git takes pathspecs literally: magic needs this
            "grep", "-n", "-E", pattern, ref, "--", *paths, env={"GIT_LITERAL_PATHSPECS": "0"}
        )
    except RuntimeError:  # no match
        return ""
    return out.replace(f"\n{ref}:", "\n").removeprefix(f"{ref}:")


def context(team, t: dict, ref: str) -> str:
    """What the Architect reads: the entry, the files its goal names, the backend routes, the
    web API clients and the task graph, all as of `ref` (dev)."""
    files = []
    for p in list(dict.fromkeys(PATH.findall(t.get("goal", ""))))[:MAX_FILES]:
        try:
            kind = team.git("cat-file", "-t", f"{ref}:{p.rstrip('/')}").strip()
        except RuntimeError:
            files.append(f"<file path='{p}'>does not exist on dev</file>")
            continue
        if kind == "tree":
            names = team.git("ls-tree", "-r", "--name-only", ref, "--", p).splitlines()
            files.append(f"<folder path='{p}'>\n" + "\n".join(names[:80]) + "\n</folder>")
        else:
            text = team.git("show", f"{ref}:{p}")[:MAX_FILE]
            files.append(f"<file path='{p}'>\n{text}\n</file>")
    tasks = team.load_backlog()
    graph = [
        {k: x[k] for k in ("id", "area", "status", "title", "depends_on") if k in x}
        for x in tasks
        if x["status"] not in ("done", "later", "dropped")
    ]
    devs = {n: {"areas": d["areas"], "owns": d["owns"]} for n, d in team.cfg["developers"].items()}
    return "\n\n".join(
        [
            f"<developers>\n{yaml.safe_dump(devs)}</developers>",
            f"<task>\n{yaml.safe_dump(t, allow_unicode=True, sort_keys=False)}</task>",
            *files,
            "<backend_routes>\n"
            + _grep(team, ref, r"@(router|app)\.(get|post|put|patch|delete)\(|APIRouter\(",
                    "backend/app")  # fmt: skip
            + "</backend_routes>",
            "<web_api_clients>\n"
            + _grep(team, ref, r"/api/|^export (async )?function", "web/src/lib",
                    ":(exclude,glob)**/*.test.*")  # fmt: skip
            + "</web_api_clients>",
            "<open_tasks>\n" + yaml.safe_dump(graph, allow_unicode=True, sort_keys=False)
            + "</open_tasks>",
            "<done_tasks>" + ", ".join(x["id"] for x in tasks if x["status"] == "done")
            + "</done_tasks>",
        ]
    )  # fmt: skip


def brief(team, t: dict) -> str | None:
    """The brief for `t` ("" when there is none), or None when `t` now waits on a new
    prerequisite. Never raises: a failed call plans without a brief."""
    ref = f"{team.remote_name}/{team.cfg['base_branch']}"
    try:
        head = team.git("rev-parse", ref).strip()
        key = hashlib.sha256(f"{t.get('goal', '')}\n{head}".encode()).hexdigest()
        cache = team.home / "architect" / f"{t['id']}.json"
        cache.parent.mkdir(exist_ok=True)
        try:
            kept = json.loads(cache.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            kept = {}
        if kept.get("key") == key:
            return kept["brief"]
        team.emit("architect", "reading the code the task touches", task=t["id"], status="working")
        out = team.agents.ask(team, "architect", t["id"], SYSTEM, context(team, t, ref), SCHEMA)
    except Exception as e:  # noqa: BLE001 (the brain plans without it)
        team.emit("architect", f"no brief: {e}", task=t["id"], status="idle")
        return ""
    pre = out.get("prerequisite") or {}
    if pre.get("title") and pre.get("area") != t.get("area"):
        try:
            new = AN.add_prerequisite(team, t["id"], pre, "; ".join(out.get("missing") or []) or
                                      pre["title"], by="architect")  # fmt: skip
            team.emit("architect", f"waits for new {new}", task=t["id"], status="idle")
            return None
        except AN.Refused as e:  # the brief still says what is missing
            team.emit("architect", f"prerequisite not added: {e}", task=t["id"])
    text = out.get("brief", "").strip()
    if missing := out.get("missing"):
        text += "\nMissing: " + "; ".join(missing)
    cache.write_text(json.dumps({"key": key, "brief": text}))
    team.emit("architect", "brief ready", task=t["id"], status="idle")
    return text
