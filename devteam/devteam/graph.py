"""The team as a LangGraph graph.

brain -> one subgraph per assigned task, run in parallel:
    worktree -> develop -> test -> (back to develop, max N loops) -> packet
Review is outside the graph (`devteam review`): nothing merges without the owner.
"""

import operator
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Annotated, Any, TypedDict

import yaml
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

AGENTS = ["brain", "dev_pipeline", "dev_app", "tester"]
SHARED_DIRS = ["docs/"]  # any developer may touch these besides its own folders


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
    def git(self, *args: str, cwd: Path | str | None = None) -> str:
        r = subprocess.run(["git", *args], cwd=cwd or self.root, capture_output=True, text=True)
        if r.returncode:
            raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()}")
        return r.stdout

    def commit_round(self, s: TaskState) -> list[str]:
        """Commit the developer's changes in its own folders; revert anything outside them."""
        wt, owns = s["worktree"], self.cfg["developers"][s["assignee"]]["owns"] + SHARED_DIRS
        stray = []
        for line in self.git("status", "--porcelain", "-uall", cwd=wt).splitlines():
            path = line[3:].split(" -> ")[-1].strip('"')
            if not any(path.startswith(d) for d in owns):
                stray.append(path)
                self.git("reset", "-q", "--", path, cwd=wt)
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
        self.git("add", "-A", cwd=wt)
        if self.git("diff", "--cached", "--name-only", cwd=wt).strip():
            msg = f"{s['task']['id']}: {s['task']['title']} (round {s.get('loops', 0) + 1})"
            self.git("commit", "-q", "-m", msg, cwd=wt)
        return stray

    def run_checks(self, s: TaskState) -> list[dict]:
        dev = self.cfg["developers"][s["assignee"]]
        out = []
        for cmd in dev.get("setup", []) + dev.get("checks", []):
            try:
                r = subprocess.run(
                    cmd, shell=True, cwd=s["worktree"], capture_output=True, text=True, timeout=1800
                )
                ok, text = r.returncode == 0, r.stdout + r.stderr
            except subprocess.TimeoutExpired:
                ok, text = False, "timed out after 30 min"
            out.append({"cmd": cmd, "ok": ok, "output": self.redact(text[-4000:])})
        return out


def build_graph(team: Team, checkpointer):
    cfg = team.cfg

    # --- per-task subgraph ---------------------------------------------------------------
    def worktree(s: TaskState):
        tid = s["task"]["id"]
        branch, path = f"{team.branch_prefix}/{tid}", team.home / "worktrees" / tid
        if not path.exists():
            if team.git("branch", "--list", branch).strip():
                team.git("worktree", "add", str(path), branch)
            else:
                team.git("worktree", "add", "-b", branch, str(path), cfg["base_branch"])
        team.emit(
            "brain",
            f"worktree {path.relative_to(team.root)} on {branch}",
            task=tid,
            to=s["assignee"],
        )
        return {"worktree": str(path), "branch": branch, "loops": 0}

    def develop(s: TaskState):
        dev, tid = s["assignee"], s["task"]["id"]
        what = "fixing: " + s["feedback"][:200] if s.get("feedback") else "building the plan"
        team.emit(dev, what, task=tid, status="working")
        out = team.agents.develop(team, s)
        stray = team.commit_round(s)
        summary = out["summary"]
        if stray:
            summary += f"\n\nReverted edits outside your folders: {', '.join(stray)}"
            team.emit(dev, f"edited outside its folders, reverted: {stray}", task=tid)
        team.emit(dev, summary[:300], task=tid, to="tester", status="idle")
        return {"session": out["session"], "dev_summary": summary}

    def test(s: TaskState):
        tid = s["task"]["id"]
        team.emit("tester", "running checks", task=tid, status="working")
        checks = [] if team.fake else team.run_checks(s)
        for c in checks:
            team.emit("tester", f"{'ok  ' if c['ok'] else 'FAIL'} {c['cmd']}", task=tid)
        verdict = team.agents.judge(team, s, checks)
        verdict["checks"] = checks
        loops = s.get("loops", 0) + 1
        if verdict["passed"]:
            team.emit("tester", "all criteria met", task=tid, to="reviewer", status="idle")
        elif loops <= cfg["max_fix_loops"]:
            team.emit(
                "tester",
                f"loop {loops}: {verdict['feedback'][:300]}",
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
        path.write_text(
            team.redact(
                f"# {tid}: {s['task']['title']}\n\n"
                f"**{'Ready for review' if v['passed'] else 'Tester gave up — needs you'}** · "
                f"developer: {s['assignee']} · branch `{s['branch']}` · "
                f"fix loops: {s['loops'] - 1}\n\n"
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
    def brain(s: TeamState):
        team.emit("brain", "reading the backlog", status="working")
        todo = [t for t in team.load_backlog() if t["status"] in ("todo", "changes_requested")]
        plans = team.agents.plan(team, todo) if todo else []
        by_id, seen, assignments = {t["id"]: t for t in todo}, set(), []
        for p in plans:  # at most one task per developer at a time
            if p["assignee"] in seen or p["assignee"] not in cfg["developers"]:
                continue
            seen.add(p["assignee"])
            task = by_id[p["task_id"]]
            team.update_task(task["id"], status="in_progress", owner=p["assignee"])
            team.emit(
                "brain",
                f"{task['title']} — plan with {len(p['criteria'])} criteria",
                task=task["id"],
                to=p["assignee"],
            )
            assignments.append(
                {
                    "task": task,
                    "assignee": p["assignee"],
                    "plan": p["plan"],
                    "criteria": p["criteria"],
                }
            )
        team.emit("brain", f"assigned {len(assignments)} task(s)", status="idle")
        return {"assignments": assignments}

    def fan_out(s: TeamState):
        return [Send("task", a) for a in s["assignments"]] or END

    top = StateGraph(TeamState)
    top.add_node("brain", brain)
    top.add_node("task", sub.compile())
    top.add_edge(START, "brain")
    top.add_conditional_edges("brain", fan_out, ["task", END])
    top.add_edge("task", END)
    return top.compile(checkpointer=checkpointer)
