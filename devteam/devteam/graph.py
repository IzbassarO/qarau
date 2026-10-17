"""The team as a LangGraph graph. No human review: automated gates decide.

brain (plan, validated) -> claim (backlog update) -> one subgraph per task, in parallel:
    worktree -> develop -> test -> gates -> finish
test or gates fail -> back to develop (max N fixes) -> then "blocked".
gates = merge latest dev in, secret scan, benchmark, push, PR into dev, GitHub CI, auto-merge.
"""

import fnmatch
import hashlib
import operator
import os
import re
import shutil
import signal
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

from devteam import gates as G
from devteam.costs import BudgetExceeded, SubscriptionLimit

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
        if VISUAL.search(" ".join(map(str, p.get("criteria") or []))) and not p.get("routes"):
            errors.append(f"{tid}: criteria mention the viewer or screenshots but list no routes")
        if p.get("routes") and dev in devs and not devs[dev].get("screenshots"):
            errors.append(f"{tid}: routes given but {dev} has no screenshots config")
    if errors:
        raise ValueError("invalid plan: " + "; ".join(errors))
    return [
        {
            "task": by_id[p["task_id"]],
            "assignee": p["assignee"],
            "plan": resume_note(by_id[p["task_id"]]) + p["plan"],
            "criteria": list(p["criteria"]),
            "routes": list(p.get("routes") or []),
        }
        for p in plans
    ]


class TaskState(TypedDict, total=False):
    task: dict
    assignee: str
    plan: str
    criteria: list[str]
    routes: list[str]  # web pages to screenshot for the tester
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
    verdict: dict
    gate: dict
    results: Annotated[list, operator.add]


class TeamState(TypedDict, total=False):
    developers: list[str]  # plan only for these (continuous mode: the one that is free)
    assignments: list[dict]
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
            "msg": self.redact(msg),
        }
        with self._lock, open(self.home / "events.log", "a") as f:
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
        with self._lock:
            data = yaml.safe_load(self.backlog_path.read_text())
            for t in data["tasks"]:
                if t["id"] == task_id:
                    for k, v in fields.items():  # None removes the field
                        if v is None:
                            t.pop(k, None)
                        else:
                            t[k] = v
            self.backlog_path.write_text(
                yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=100)
            )

    # --- processes (all killable by `devteam stop`) --------------------------------------
    def run_proc(
        self, cmd, cwd: str, timeout: int, input: str | None = None, env: dict | None = None
    ) -> dict:
        """Runs in its own process group, registered so stop() can kill the whole tree."""
        p = subprocess.Popen(
            cmd,
            shell=isinstance(cmd, str),
            cwd=cwd,
            text=True,
            stdin=subprocess.PIPE if input else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            env=env,
        )
        with self._lock:
            self._procs.add(p)
        try:
            out, err = p.communicate(input, timeout=timeout)
            ok = p.returncode == 0
        except subprocess.TimeoutExpired:
            os.killpg(p.pid, signal.SIGKILL)
            out, err = p.communicate()
            ok, err = False, (err or "") + f"\ntimed out after {timeout // 60} min"
        finally:
            with self._lock:
                self._procs.discard(p)
        return {
            "ok": ok,
            "code": p.returncode,
            "stdout": out or "",
            "stderr": err or "",
            "output": (out or "") + (err or ""),
        }

    def kill_all(self):
        with self._lock:
            for p in list(self._procs):
                try:
                    os.killpg(p.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass

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
        with self._git_lock:
            r = subprocess.run(  # bytes, not text=True: that would turn \r in file names into \n
                ["git", *args],
                cwd=cwd or self.root,
                capture_output=True,
                input=input.encode("utf-8", "surrogateescape") if input is not None else None,
                env={**os.environ, "GIT_LITERAL_PATHSPECS": "1", **(env or {})},
            )
        if r.returncode:
            raise RuntimeError(f"git {' '.join(args)}: {r.stderr.decode(errors='replace').strip()}")
        return r.stdout.decode("utf-8", "surrogateescape")

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
        """Blocked tasks whose depends_on are all done go back to todo, pushed to dev."""
        tasks = self.load_backlog()
        done = {t["id"] for t in tasks if t["status"] == "done"}
        ready = [
            t["id"]
            for t in tasks
            if t["status"] == "blocked" and t.get("depends_on") and set(t["depends_on"]) <= done
        ]
        for tid in ready:
            self.update_task(tid, status="todo", blocked_reason=None)
            self.push_status(tid, f"backlog: {tid} todo (dependencies done)")
            self.emit("brain", "dependencies done, back to todo", task=tid)
        return ready

    def push_status(self, task_id: str, message: str) -> str | None:
        """Commits this task's status fields onto origin/dev and pushes them straight to dev:
        dev holds the record (a status kept only in the working copy got lost on 2026-10-16).
        Built on a fresh origin/dev each try, so a race costs a retry; never forced or rebased.
        The pre-push hook lets through only a fast-forward that changes backlog.yaml alone."""
        base, rn, err = self.cfg["base_branch"], self.remote_name, None
        with self._lock:  # no emit() inside: it takes the same lock
            mine = next(t for t in self.load_backlog() if t["id"] == task_id)
            for attempt in range(3):
                self.fetch(base)
                parent = self.git("rev-parse", f"{rn}/{base}").strip()
                data = yaml.safe_load(self.git("show", f"{parent}:devteam/backlog.yaml"))
                for t in data["tasks"]:
                    if t["id"] == task_id:
                        for k in (*STATUS_FIELDS, "blocked_reason"):
                            if k in mine:
                                t[k] = mine[k]
                            else:
                                t.pop(k, None)
                text = yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=100)
                blob = self.git("hash-object", "-w", "--stdin", input=text).strip()
                env = {"GIT_INDEX_FILE": str(self.home / "status.index")}
                self.git("read-tree", parent, env=env)
                self.git(
                    "update-index", "--cacheinfo", f"100644,{blob},devteam/backlog.yaml", env=env
                )
                tree = self.git("write-tree", env=env).strip()
                if tree == self.git("rev-parse", f"{parent}^{{tree}}").strip():
                    break  # dev already says so (a resumed finish)
                self._status_commit = self.git(
                    "commit-tree", tree, "-p", parent, "-m", message
                ).strip()
                try:
                    self.git(
                        "push", "-q", self.remote_url, f"{self._status_commit}:refs/heads/{base}"
                    )
                    break
                except RuntimeError:  # dev moved meanwhile: rebuild on the new tip
                    if attempt == 2:
                        raise
                finally:
                    self._status_commit = None
            if not self.fake and self.git("branch", "--show-current").strip() == base:
                try:  # keep the main checkout on dev in step; local edits ride along
                    self.git("merge", "-q", "--ff-only", "--autostash", f"{rn}/{base}")
                except RuntimeError as e:
                    err = e
        if err:
            self.emit("brain", f"could not fast-forward the checkout: {err}; pull by hand")
        return message

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
        owns = self.cfg["developers"][s["assignee"]]["owns"] + SHARED_DIRS
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
            ok = all(any(p.startswith(d) for d in owns) and not is_protected(p) for p in paths)
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
            out.append(
                {
                    "cmd": cmd,
                    "kind": kind,
                    "ok": r["ok"],
                    "output": self.redact(r["output"][-4000:]),
                }
            )
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
        gate_lines = "\n".join(f"- {line}" for line in g.get("lines", []))
        if g.get("reason"):
            gate_lines += f"\n- **stopped:** {g['reason']}"
        shots = "\n".join(  # local files: a PR body can't embed them without an upload
            f"- `{Path(p).relative_to(self.root)}`" for p in v.get("screenshots", [])
        )
        shots += "".join(f"\n- **failed:** {x}" for x in v.get("screenshot_problems", []))
        path.write_text(
            self.redact(
                f"# {tid}: {s['task']['title']}\n\n"
                f"**{status}** · developer: {s['assignee']} · branch `{s['branch']}` · "
                f"rounds: {s.get('rounds', 0)} · sent back: {s.get('fixes', 0)} · "
                f"judge: {'pass' if v.get('judge_passed') else 'fail'}\n\n"
                f"## What changed\n\n{v.get('summary', '')}\n\n"
                f"## How to try it\n\n{v.get('how_to_try', '')}\n\n"
                f"## Acceptance criteria\n\n{crit}\n\n## Local checks\n\n{checks or '- (none)'}\n\n"
                f"## Gates\n\n{gate_lines or '- (not reached)'}\n\n"
                + (
                    f"## Screenshots (dev machine, seen by the tester)\n\n{shots}\n\n"
                    if shots
                    else ""
                )
                + f"## Files\n\n```\n{stat}```\n\n🤖 Automated by devteam — no human review.\n"
            )
        )
        return path


def build_graph(team: Team, checkpointer):
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
            else:
                team.git("worktree", "add", "-b", branch, str(path), base_ref)
        out = {"worktree": str(path), "branch": branch, "rounds": 0, "fixes": 0}
        team.verify_worktree({**s, **out})
        # earlier work starts from today's dev: a merge, never a rebase (no-op on a new branch)
        r = subprocess.run(
            ["git", "merge", "--no-edit", base_ref], cwd=path, capture_output=True, text=True
        )
        if r.returncode:
            subprocess.run(["git", "merge", "--abort"], cwd=path, capture_output=True)
            team.emit("brain", f"could not merge {base} into {branch}; the gates will", task=tid)
        out["head"] = out["start"] = team.git("rev-parse", "HEAD", cwd=path).strip()
        out["snap"] = team.snapshot(str(path))
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
        what = "fixing: " + s["feedback"][:200] if s.get("feedback") else "building the plan"
        (team.home / "scratch" / tid).mkdir(parents=True, exist_ok=True)  # see the brief
        team.emit(dev, what, task=tid, status="working")
        if not team.fake:  # e.g. pnpm install: the Codex sandbox has no network
            for cmd in team.cfg["developers"][dev].get("setup", []):
                r = team.run_proc(cmd, cwd=wt, timeout=1800)
                if not r["ok"]:
                    raise RuntimeError(f"setup `{cmd}` failed: {team.redact(r['output'][-800:])}")
        out = team.agents.develop(team, s)
        res = team.commit_round(s, s["head"], s["snap"])
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
        }

    def test(s: TaskState):
        tid = s["task"]["id"]
        team.emit("tester", "running checks", task=tid, status="working")
        checks = [] if team.fake else team.run_checks(s)
        for c in checks:
            team.emit("tester", f"{'ok  ' if c['ok'] else 'FAIL'} {c['cmd']}", task=tid)
        shots, problems = [], []
        dev_shots: list[str] = []
        if not team.fake:
            shots, problems = G.screenshots(team, s, s.get("routes") or [])
            dev_shots, dev_problems = G.dev_screenshots(team, s, s.get("routes") or [])
            problems += dev_problems
            if shots or problems:
                team.emit(
                    "tester", f"screenshots: {len(shots)} ok, {len(problems)} failed", task=tid
                )
        verdict = team.agents.judge(
            team, {**s, "screenshots": shots + dev_shots, "screenshot_problems": problems}, checks
        )
        verdict["screenshots"], verdict["screenshot_problems"] = shots + dev_shots, problems
        failed = [c for c in checks if not c["ok"]]
        verdict["judge_passed"] = verdict["passed"]
        # a visual criterion passes only on the tester's own screenshots, one per route
        no_shots = len(shots) < len(s.get("routes") or [])
        verdict["passed"] = verdict["passed"] and not failed and not no_shots
        if no_shots:
            verdict["feedback"] = (
                f"{verdict.get('feedback', '')}\n\nTester screenshots missing for "
                f"{s['routes']}: visual criteria cannot pass without them.\n" + "\n".join(problems)
            )
        if failed:
            logs = "\n".join(f"$ {c['cmd']}\n{c['output'][-1500:]}" for c in failed)
            verdict["feedback"] = f"{verdict.get('feedback', '')}\n\nFailing checks:\n{logs}"
        verdict["checks"] = checks
        snap = team.snapshot(s["worktree"])  # files the checks made are not the developer's
        fixes = s.get("fixes", 0) + (0 if verdict["passed"] else 1)
        if verdict["passed"]:
            team.emit(
                "tester", "local checks and criteria pass", task=tid, to="gates", status="idle"
            )
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
        out = {"verdict": verdict, "fixes": fixes, "snap": snap, "gate": {}}
        if not verdict["passed"]:
            out["feedback"] = verdict["feedback"]
            out["gate"] = {"reason": "tester: " + verdict["feedback"].strip()[:500]}
        return out

    def after_test(s: TaskState):
        if s["verdict"]["passed"]:
            return "gates"
        return "develop" if s["fixes"] <= max_fixes else "finish"

    def gates(s: TaskState):
        tid, wt, g = s["task"]["id"], s["worktree"], cfg["gates"]
        lines: list[str] = []
        team.emit(
            "tester", "gates: merge dev, secrets, benchmark, PR, CI", task=tid, status="working"
        )

        def stop(reason: str, *, block: bool = False, head: str | None = None):
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
            # ponytail: conflicts block the task; add a "developer resolves markers" round
            # if they become common (parallel tasks own different folders, so rare)
            return stop(f"conflicts with {base} in {files}", block=True)
        head = team.git("rev-parse", "HEAD", cwd=wt).strip()
        lines.append(f"up to date with `{base}` @ {base_sha[:8]}")
        # 2. Nothing with a secret is ever pushed (history can't be rewritten afterwards).
        leaks = G.gitleaks(team, wt, f"--log-opts={base_ref}..HEAD")
        if leaks:
            return stop(f"secret scan found {leaks}; not pushed", block=True, head=head)
        lines.append("secret scan (gitleaks): clean")
        # 3. Benchmark, when the change can affect it.
        bcfg = g["benchmark"]
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
            if reg := G.regressions(before, after, bcfg):
                return stop("benchmark regression vs dev:\n- " + "\n- ".join(reg), head=head)
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
        if merged:
            team.unblock_ready()  # e.g. A4 done -> P3b back to todo
        if merged:  # finished: the worktree is no longer needed (branch and packet stay)
            try:
                team.git("worktree", "remove", "--force", s["worktree"])
            except RuntimeError:
                pass
        team.emit("tester", f"{status}: {packet.relative_to(team.root)}", task=tid, to="reviewer")
        return {
            "results": [{"id": tid, "status": status, "packet": str(packet), "pr": g.get("pr", "")}]
        }

    def loud(name, fn):
        """A parallel task's error surfaces only when the other tasks finish (hours): say it
        now, so the owner can `devteam stop`, fix and `resume`."""

        def node(s: TaskState):
            if team.stopping.is_set():
                raise StopRun(f"stopped before {name}")
            try:
                return fn(s)
            except (SubscriptionLimit, BudgetExceeded):  # the caller waits or stops, no crash
                raise
            except Exception as e:
                team.emit(
                    s.get("assignee", "brain"),
                    f"CRASHED in {name}: {e}. The run stops when the other tasks finish; "
                    "`devteam stop` now, fix, then `devteam resume`.",
                    task=s["task"]["id"],
                    status="idle",
                )
                raise

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
    sub.add_edge("worktree", "develop")
    sub.add_edge("develop", "test")
    sub.add_conditional_edges("test", after_test, ["gates", "develop", "finish"])
    sub.add_conditional_edges("gates", after_gates, ["develop", "finish"])
    sub.add_edge("finish", END)

    # --- top level -----------------------------------------------------------------------
    def brain(s: TeamState, config: RunnableConfig):
        """Plans only; writes nothing. Tasks this run already claimed count as open again."""
        run = config["configurable"]["thread_id"]
        if team.stopping.is_set():
            raise StopRun("stopped before planning")
        devs = {d: cfg["developers"][d] for d in s.get("developers") or cfg["developers"]}
        team.emit("brain", "reading the backlog", status="working")
        todo = [
            t
            for t in team.load_backlog()
            if t["status"] == "todo" or (t["status"] == "in_progress" and t.get("run") == run)
        ]
        # Never redo landed work, even if the backlog lost its status (2026-10-16: P0, A2a).
        if again := sorted(t["id"] for t in todo if t["id"] in team.merged_tasks()):
            team.emit("brain", f"skipping tasks already merged into dev, mark them done: {again}")
            todo = [t for t in todo if t["id"] not in again]
        done = {t["id"] for t in team.load_backlog() if t["status"] == "done"}
        if waiting := sorted(t["id"] for t in todo if not set(t.get("depends_on") or []) <= done):
            team.emit("brain", f"skipping tasks whose depends_on are not all done: {waiting}")
            todo = [t for t in todo if t["id"] not in waiting]
        for t in todo:  # merged_tasks() fetched the task branches
            if work := team.prior_work(t["id"]):
                t["previous_work"] = work
        if team.only:  # `devteam run --only P1`
            todo = [t for t in todo if t["id"] in team.only]
        known = {a for d in cfg["developers"].values() for a in d["areas"]}
        if no_area := [t["id"] for t in todo if t.get("area") not in known]:
            team.emit("brain", f"skipping tasks without a known area {sorted(known)}: {no_area}")
        areas = {a for d in devs.values() for a in d["areas"]}  # other developers' tasks wait
        todo = [t for t in todo if t.get("area") in areas]
        assignments, feedback = [], None
        for attempt in range(2):  # one retry with the validation errors
            plans = team.agents.plan(team, todo, feedback=feedback, devs=list(devs)) if todo else []
            try:
                assignments = validate_plan(plans, todo, {**cfg, "developers": devs})
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
