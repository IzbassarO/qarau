"""The team as a LangGraph graph.

brain (plan, validated) -> claim (backlog update) -> one subgraph per task, in parallel:
    worktree -> develop -> test -> (back to develop, max N loops) -> packet
Review is outside the graph (`devteam review`): nothing merges without the owner.
"""

import fnmatch
import operator
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Annotated, Any, TypedDict

import yaml
from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

AGENTS = ["brain", "dev_pipeline", "dev_app", "tester"]
SHARED_DIRS = ["docs/"]  # any developer may touch these besides its own folders
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


class Violation(RuntimeError):
    """The worktree or branch is not what the graph expects: stop, never commit."""


def is_protected(path: str) -> bool:
    def match(pats):
        return any(fnmatch.fnmatch(path, p) for p in pats)

    return match(PROTECTED) and not match(PROTECTED_OK)


def validate_plan(plans: list[dict], todo: list[dict], cfg: dict) -> list[dict]:
    """The whole plan is valid or nothing happens. Returns assignments."""
    by_id = {t["id"]: t for t in todo}
    devs = cfg["developers"]
    ids = [p.get("task_id") for p in plans]
    who = [p.get("assignee") for p in plans]
    errors = []
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
        if not str(p.get("plan", "")).strip() or not p.get("criteria"):
            errors.append(f"{tid}: plan and acceptance criteria must not be empty")
    if errors:
        raise ValueError("invalid plan: " + "; ".join(errors))
    return [
        {
            "task": by_id[p["task_id"]],
            "assignee": p["assignee"],
            "plan": p["plan"],
            "criteria": list(p["criteria"]),
        }
        for p in plans
    ]


class TaskState(TypedDict, total=False):
    task: dict
    assignee: str
    plan: str
    criteria: list[str]
    worktree: str
    branch: str
    session: str
    dev_summary: str
    feedback: str
    loops: int
    verdict: dict
    results: Annotated[list, operator.add]


class TeamState(TypedDict, total=False):
    assignments: list[dict]
    results: Annotated[list, operator.add]


class Team:
    """Everything the nodes need: paths, config, secrets, the agent implementations."""

    def __init__(self, root: Path, cfg: dict, secrets: dict, agents: Any, fake: bool = False):
        self.root, self.cfg, self.secrets, self.agents, self.fake = root, cfg, secrets, agents, fake
        self.home = root / "devteam" / ".state" / ("fake" if fake else "")
        self.home.mkdir(parents=True, exist_ok=True)
        self.branch_prefix = "devteam-fake" if fake else "devteam"
        real_backlog = root / "devteam" / "backlog.yaml"
        self.backlog_path = self.home / "backlog.yaml" if fake else real_backlog
        if fake and not self.backlog_path.exists():
            shutil.copy(real_backlog, self.backlog_path)
        self._lock = threading.Lock()  # ponytail: one lock for backlog + log, fine for 2 devs

    # --- logging / live view -------------------------------------------------------------
    def redact(self, text: str) -> str:
        for v in self.secrets.values():
            if v and len(v) > 8:
                text = text.replace(v, "***")
        return text

    def emit(self, agent: str, msg: str, *, task: str = "", to: str = "", status: str = ""):
        event = {
            "t": time.strftime("%H:%M:%S"),
            "agent": agent,
            "task": task,
            "to": to,
            "status": status,
            "msg": self.redact(msg),
        }
        with self._lock, open(self.home / "events.log", "a") as f:
            f.write(yaml.safe_dump([event], allow_unicode=True, width=1000))
        try:
            get_stream_writer()(event)
        except RuntimeError:  # called outside a graph run
            pass

    # --- backlog -------------------------------------------------------------------------
    def load_backlog(self) -> list[dict]:
        return yaml.safe_load(self.backlog_path.read_text())["tasks"]

    def update_task(self, task_id: str, **fields):
        with self._lock:
            data = yaml.safe_load(self.backlog_path.read_text())
            for t in data["tasks"]:
                if t["id"] == task_id:
                    t.update(fields)
            self.backlog_path.write_text(
                yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=100)
            )

    # --- git -----------------------------------------------------------------------------
    def git(
        self,
        *args: str,
        cwd: Path | str | None = None,
        input: str | None = None,
        env: dict | None = None,
    ) -> str:
        r = subprocess.run(
            ["git", *args],
            cwd=cwd or self.root,
            capture_output=True,
            text=True,
            input=input,
            env={**os.environ, "GIT_LITERAL_PATHSPECS": "1", **(env or {})},
        )
        if r.returncode:
            raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()}")
        return r.stdout

    def branch_for(self, task_id: str) -> str:
        return f"{self.branch_prefix}/{task_id}"

    def verify_worktree(self, s: TaskState):
        """The worktree must be registered with git and sit on exactly devteam/<task>."""
        wt, branch = Path(s["worktree"]).resolve(), self.branch_for(s["task"]["id"])
        if branch in (self.cfg["base_branch"], "main", "master"):
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

    def snapshot(self, wt: str) -> dict[str, tuple[int, int]]:
        """Size and mtime of every protected file, tracked or ignored."""
        snap = {}
        for dirpath, dirnames, filenames in os.walk(wt):
            dirnames[:] = [d for d in dirnames if d not in SNAPSHOT_SKIP]
            for name in filenames:
                full = os.path.join(dirpath, name)
                rel = os.path.relpath(full, wt)
                if is_protected(rel):
                    st = os.lstat(full)
                    snap[rel] = (st.st_size, st.st_mtime_ns)
        return snap

    def commit_round(self, s: TaskState, head_before: str, snap_before: dict) -> list[str]:
        """Commit the developer's changes in its own folders, staging only checked paths.
        Reverts edits outside them and protected-file changes. Returns what it refused."""
        self.verify_worktree(s)
        wt = s["worktree"]
        owns = self.cfg["developers"][s["assignee"]]["owns"] + SHARED_DIRS
        if self.git("rev-parse", "HEAD", cwd=wt).strip() != head_before:
            self.git("reset", "-q", "--soft", head_before, cwd=wt)  # its commits get checked too
        self.git("reset", "-q", cwd=wt)  # unstage everything; we decide what goes in

        snap_after = self.snapshot(wt)
        refused = {
            p for p in set(snap_before) | set(snap_after) if snap_before.get(p) != snap_after.get(p)
        }

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
            ok = all(any(p.startswith(d) for d in owns) and not is_protected(p) for p in paths)
            if ok:
                allowed.extend(paths)
            else:
                refused.update(paths)

        for path in sorted(refused):
            in_head = (
                subprocess.run(
                    ["git", "cat-file", "-e", f"HEAD:{path}"], cwd=wt, capture_output=True
                ).returncode
                == 0
            )
            if in_head:
                self.git("checkout", "HEAD", "--", path, cwd=wt)
            else:
                (Path(wt) / path).unlink(missing_ok=True)

        allowed = [p for p in allowed if p not in refused]
        if allowed:
            self.git(
                "add",
                "-A",
                "--pathspec-from-file=-",
                "--pathspec-file-nul",
                cwd=wt,
                input="\0".join(allowed),
            )
        if self.git("diff", "--cached", "--name-only", cwd=wt).strip():
            self.verify_worktree(s)
            msg = f"{s['task']['id']}: {s['task']['title']} (round {s.get('loops', 0) + 1})"
            self.git("commit", "-q", "--no-verify", "-m", msg, cwd=wt)
        return sorted(refused)

    def cleanup(self) -> list[str]:
        """Remove worktrees of tasks the owner has decided on (done / dropped).
        Branches and review packets stay. Refuses dirty worktrees (git's own check)."""
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
            try:
                r = subprocess.run(
                    cmd, shell=True, cwd=s["worktree"], capture_output=True, text=True, timeout=1800
                )
                ok, text = r.returncode == 0, r.stdout + r.stderr
            except subprocess.TimeoutExpired:
                ok, text = False, "timed out after 30 min"
            out.append({"cmd": cmd, "kind": kind, "ok": ok, "output": self.redact(text[-4000:])})
            if kind == "setup" and not ok:
                break
        return out


def build_graph(team: Team, checkpointer):
    cfg = team.cfg

    # --- per-task subgraph ---------------------------------------------------------------
    def worktree(s: TaskState):
        tid = s["task"]["id"]
        branch, path = team.branch_for(tid), team.home / "worktrees" / tid
        if not path.exists():
            if team.git("branch", "--list", branch).strip():
                team.git("worktree", "add", str(path), branch)
            else:
                team.git("worktree", "add", "-b", branch, str(path), cfg["base_branch"])
        out = {"worktree": str(path), "branch": branch, "loops": 0}
        team.verify_worktree({**s, **out})
        team.emit(
            "brain",
            f"worktree {path.relative_to(team.root)} on {branch}",
            task=tid,
            to=s["assignee"],
        )
        return out

    def develop(s: TaskState):
        dev, tid, wt = s["assignee"], s["task"]["id"], s["worktree"]
        team.verify_worktree(s)
        head, snap = team.git("rev-parse", "HEAD", cwd=wt).strip(), team.snapshot(wt)
        what = "fixing: " + s["feedback"][:200] if s.get("feedback") else "building the plan"
        team.emit(dev, what, task=tid, status="working")
        out = team.agents.develop(team, s)
        refused = team.commit_round(s, head, snap)
        summary = out["summary"]
        if refused:
            summary += (
                "\n\nThe orchestrator reverted your changes to files you may not touch "
                f"(outside your folders, or protected): {', '.join(refused)}"
            )
            team.emit(dev, f"touched forbidden files, reverted: {refused}", task=tid)
        team.emit(dev, summary[:300], task=tid, to="tester", status="idle")
        return {"session": out["session"], "dev_summary": summary}

    def test(s: TaskState):
        tid = s["task"]["id"]
        team.emit("tester", "running checks", task=tid, status="working")
        checks = [] if team.fake else team.run_checks(s)
        for c in checks:
            team.emit("tester", f"{'ok  ' if c['ok'] else 'FAIL'} {c['cmd']}", task=tid)
        verdict = team.agents.judge(team, s, checks)
        failed = [c for c in checks if not c["ok"]]
        verdict["judge_passed"] = verdict["passed"]
        verdict["passed"] = verdict["passed"] and not failed  # both, or it is not done
        if failed:
            logs = "\n".join(f"$ {c['cmd']}\n{c['output'][-1500:]}" for c in failed)
            verdict["feedback"] = f"{verdict.get('feedback', '')}\n\nFailing checks:\n{logs}"
        verdict["checks"] = checks
        loops = s.get("loops", 0) + 1
        if verdict["passed"]:
            team.emit(
                "tester", "all checks and criteria pass", task=tid, to="reviewer", status="idle"
            )
        elif loops <= cfg["max_fix_loops"]:
            team.emit(
                "tester",
                f"loop {loops}: {verdict['feedback'].strip()[:300]}",
                task=tid,
                to=s["assignee"],
                status="idle",
            )
        else:
            team.emit(
                "tester",
                f"giving up after {loops - 1} fix loops",
                task=tid,
                to="reviewer",
                status="idle",
            )
        return {"verdict": verdict, "loops": loops, "feedback": verdict["feedback"]}

    def after_test(s: TaskState):
        if s["verdict"]["passed"] or s["loops"] > cfg["max_fix_loops"]:
            return "packet"
        return "develop"

    def packet(s: TaskState):
        tid, v = s["task"]["id"], s["verdict"]
        status = "review" if v["passed"] else "failed"
        path = team.home / "packets" / f"{tid}.md"
        path.parent.mkdir(exist_ok=True)
        base = cfg["base_branch"]
        stat = team.git("diff", "--stat", f"{base}...{s['branch']}", cwd=s["worktree"])
        crit = "\n".join(
            f"- [{'x' if c.get('met') else ' '}] {c['criterion']}"
            + (f" — {c['note']}" if c.get("note") else "")
            for c in v.get("criteria", [])
        )
        checks = "\n".join(f"- {'ok' if c['ok'] else 'FAIL'}: `{c['cmd']}`" for c in v["checks"])
        shots = "\n".join(f"![]({p})" for p in v.get("screenshots", []))
        head = "Ready for review" if v["passed"] else "Not done — needs you"
        path.write_text(
            team.redact(
                f"# {tid}: {s['task']['title']}\n\n"
                f"**{head}** · developer: {s['assignee']} · branch `{s['branch']}` · "
                f"fix loops: {s['loops'] - 1} · "
                f"judge: {'pass' if v['judge_passed'] else 'fail'}\n\n"
                f"## What changed\n\n{v.get('summary', '')}\n\n"
                f"## How to try it\n\n{v.get('how_to_try', '')}\n\n"
                f"## Acceptance criteria\n\n{crit}\n\n## Checks\n\n{checks or '- (none)'}\n\n"
                + (f"## Screenshots\n\n{shots}\n\n" if shots else "")
                + f"## Files\n\n```\n{stat}```\n"
            )
        )
        team.update_task(tid, status=status, branch=s["branch"])
        team.emit(
            "tester", f"review packet: {path.relative_to(team.root)}", task=tid, to="reviewer"
        )
        return {"results": [{"id": tid, "status": status, "packet": str(path)}]}

    sub = StateGraph(TaskState)
    for name, fn in [
        ("worktree", worktree),
        ("develop", develop),
        ("test", test),
        ("packet", packet),
    ]:
        sub.add_node(name, fn)
    sub.add_edge(START, "worktree")
    sub.add_edge("worktree", "develop")
    sub.add_edge("develop", "test")
    sub.add_conditional_edges("test", after_test, ["develop", "packet"])
    sub.add_edge("packet", END)

    # --- top level -----------------------------------------------------------------------
    def brain(s: TeamState, config: RunnableConfig):
        """Plans only; writes nothing. Tasks this run already claimed count as open again."""
        run = config["configurable"]["thread_id"]
        team.emit("brain", "reading the backlog", status="working")
        todo = [
            t
            for t in team.load_backlog()
            if t["status"] in ("todo", "changes_requested")
            or (t["status"] == "in_progress" and t.get("run") == run)
        ]
        assignments, feedback = [], None
        for attempt in range(2):  # one retry with the validation errors
            plans = team.agents.plan(team, todo, feedback=feedback) if todo else []
            try:
                assignments = validate_plan(plans, todo, cfg)
                break
            except ValueError as e:
                if attempt:
                    raise
                feedback = str(e)
                team.emit("brain", f"plan rejected, re-planning: {e}")
        team.emit("brain", f"planned {len(assignments)} task(s)", status="idle")
        return {"assignments": assignments}

    def claim(s: TeamState, config: RunnableConfig):
        """Writes the checkpointed assignments to the backlog. Idempotent."""
        run = config["configurable"]["thread_id"]
        for a in s["assignments"]:
            team.update_task(a["task"]["id"], status="in_progress", owner=a["assignee"], run=run)
            team.emit(
                "brain",
                f"{a['task']['title']} — plan with {len(a['criteria'])} criteria",
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
