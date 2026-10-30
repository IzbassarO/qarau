"""End-to-end run of the fake team on a throwaway repo: usage limit + resume, crash isolation,
status pushes to dev and the backlog check before them, parallel worktrees."""

import copy
import os
import sqlite3
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from conftest import CFG, HERE, git, remote_of, team_for
from langgraph.checkpoint.sqlite import SqliteSaver
from test_safety import StubClient

from devteam import continuous as C
from devteam import real as R
from devteam.agents import FakeAgents
from devteam.costs import SubscriptionLimit
from devteam.graph import Team, build_graph
from devteam.real import llm_call


class Fast(FakeAgents):
    delay = 0


def make(repo, agents=None, fake=True, **cfg_over):
    cfg = copy.deepcopy(CFG)
    cfg.update(cfg_over)
    team = Team(repo, cfg, {}, agents or Fast(), fake=fake, remote=remote_of(repo))
    graph = build_graph(team, SqliteSaver(sqlite3.connect(":memory:", check_same_thread=False)))
    return team, graph


def task(team, tid):
    return next(t for t in team.load_backlog() if t["id"] == tid)


class CrashOnce(FakeAgents):
    delay = 0
    crashed = False
    dev_calls = 0

    def develop(self, team, s):
        CrashOnce.dev_calls += 1
        (Path(s["worktree"]) / "README.md").write_text("stray edit\n")  # outside its folders
        (Path(s["worktree"]) / "stray.txt").write_text("new stray file\n")
        return super().develop(team, s)

    def judge(self, team, s, checks):
        if not CrashOnce.crashed:
            CrashOnce.crashed = True
            raise SubscriptionLimit("usage limit", None)  # stops the run; a crash would block
        return super().judge(team, s, checks)


def test_fake_team_runs_parallel_loops_and_resumes(repo):
    team = Team(repo, CFG, {}, CrashOnce(), fake=True, remote=remote_of(repo))
    db = sqlite3.connect(team.home / "c.sqlite", check_same_thread=False)
    graph = build_graph(team, SqliteSaver(db))
    config = {"configurable": {"thread_id": "t1"}}

    try:
        graph.invoke({}, config)
        raise AssertionError("expected the limit")
    except RuntimeError as e:
        assert "usage limit" in str(e)
    calls_before = CrashOnce.dev_calls
    graph.invoke(None, config)  # resume

    results = graph.get_state(config).values["results"]
    assert sorted(r["status"] for r in results) == ["done", "done"]  # T-001 + T-002 parallel
    assert len(team.gh.merged) == 2  # both PRs auto-merged after the gates
    owners = {t["id"]: t.get("owner") for t in team.load_backlog()}
    assert owners["T-001"] == "dev_pipeline" and owners["T-002"] == "dev_app"
    assert {t["id"]: t["status"] for t in team.load_backlog()}["T-003"] == "todo"
    # resume did not redo finished developer rounds: each task = 2 rounds, 4 in total
    assert CrashOnce.dev_calls == 4 and calls_before >= 1
    log = subprocess.run(
        ["git", "log", "--oneline", "devteam-fake/T-002"], cwd=repo, capture_output=True, text=True
    ).stdout
    assert "(round 2)" in log
    assert (team.home / "packets" / "T-001.md").exists()
    remote_dev = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", "dev"],
        cwd=remote_of(repo),
        capture_output=True,
        text=True,
    ).stdout
    assert "pipeline/devteam-fake-T-001.txt" in remote_dev
    assert "web/devteam-fake-T-002.txt" in remote_dev
    files = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", "devteam-fake/T-001"],
        cwd=repo,
        capture_output=True,
        text=True,
    ).stdout.split()
    assert "stray.txt" not in files and "pipeline/devteam-fake-T-001.txt" in files
    readme = subprocess.run(
        ["git", "show", "devteam-fake/T-001:README.md"], cwd=repo, capture_output=True, text=True
    ).stdout
    assert readme == "x\n"


# --- 1. dev CI: the real-backlog test survives every status Kerege writes -----------------------
@pytest.mark.usefixtures("fast_poll")
@pytest.mark.parametrize("status", ["done", "blocked", "in_progress"])
def test_backlog_check_passes_whatever_status_kerege_writes(tmp_path, status):
    """778e072 (`backlog: A9-fix done (#41)`) turned dev red: a test asserted live statuses.
    The configured check (gates.backlog_check, every `backlog`-marked test) on the real backlog
    with every status changed, as a status commit would push it."""
    data = yaml.safe_load((HERE / "backlog.yaml").read_text())
    for t in data["tasks"]:
        t["status"] = status
    path = tmp_path / "backlog.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    cmd = yaml.safe_load((HERE / "config.yaml").read_text())["gates"]["backlog_check"]
    cmd = cmd.format(devteam=HERE)  # dev's devteam/ in Kerege: this checkout here
    r = subprocess.run(
        cmd, shell=True, cwd=HERE, env={**os.environ, "DEVTEAM_BACKLOG": str(path)},
        capture_output=True, text=True,
    )  # fmt: skip
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-1000:]
    assert " passed" in r.stdout and "deselected" in r.stdout  # only the backlog tests ran


NO_T001_DONE = (
    f'{sys.executable} -c "import os, sys, yaml; t = yaml.safe_load(open(os.environ['
    "'DEVTEAM_BACKLOG']))['tasks']; sys.exit('FAILED test_x: T-001 is done' if any(x['id'] == "
    "'T-001' and x['status'] == 'done' for x in t) else 0)\""
)


@pytest.mark.usefixtures("fast_poll")
def test_a_status_commit_that_fails_the_backlog_check_is_not_pushed(repo):
    gates = {**copy.deepcopy(CFG["gates"]), "backlog_check": NO_T001_DONE}
    team, _ = make(repo, fake=False, gates=gates)
    git(repo, "checkout", "-q", "-b", "work")  # the dev checkout fast-forward is tested elsewhere

    def dev():
        team.fetch("dev")
        return yaml.safe_load(team.git("show", f"{team.remote_name}/dev:devteam/backlog.yaml"))[
            "tasks"
        ]

    team.update_task("T-002", status="done")
    assert team.push_status("T-002", "backlog: T-002 done") == "backlog: T-002 done"
    assert next(t for t in dev() if t["id"] == "T-002")["status"] == "done"
    team.update_task("T-001", status="done")
    assert team.push_status("T-001", "backlog: T-001 done") is None  # refused, not pushed
    assert next(t for t in dev() if t["id"] == "T-001")["status"] == "todo"
    assert task(team, "T-001")["status"] == "done"  # kept locally, shown as drift
    log = (team.home / "events.log").read_text()
    assert "`backlog: T-001 done` not pushed to dev" in log and "T-001 is done" in log


@pytest.mark.usefixtures("fast_poll")
def test_the_backlog_check_runs_the_fetched_devs_tests_not_the_checkouts(repo, tmp_path):
    """2026-10-29 14:33: a PR merged into dev changed the backlog and its test; the main checkout
    was still on the older test and refused a status that dev's own test passes."""
    (repo / "devteam" / "check.sh").write_text("exit 1  # the old test\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "old test")
    git(repo, "push", "-q", remote_of(repo), "dev")
    other = tmp_path / "other"  # the merged PR, the main checkout not pulled
    git(tmp_path, "clone", "-q", "-b", "dev", remote_of(repo), str(other))
    (other / "devteam" / "check.sh").write_text("exit 0  # the new test\n")
    git(other, "commit", "-qam", "new test")
    git(other, "push", "-q", "origin", "dev")
    for cmd, pushed in (("sh check.sh", False), ("sh {devteam}/check.sh", True)):
        gates = {**copy.deepcopy(CFG["gates"]), "backlog_check": cmd}
        team, _ = make(repo, fake=False, gates=gates)
        team.update_task("T-002", status="done")
        assert (team.push_status("T-002", "backlog: T-002 done") is not None) == pushed


# --- 1. developer CLI: absolute path, checked before each round, one retry --------------------
@pytest.mark.usefixtures("fast_poll")
def test_tool_path_is_checked_and_looked_up_again_after_a_wait(monkeypatch, tmp_path):
    monkeypatch.setattr(R, "TOOL_RETRY_S", 0)
    exe = tmp_path / "claude"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    found = iter([None, str(exe)])  # gone (mid self-update), back after the wait
    monkeypatch.setattr(R.shutil, "which", lambda name: next(found))
    agents = R.RealAgents.__new__(R.RealAgents)
    agents.paths = {"claude": str(tmp_path / "vanished")}
    team = SimpleNamespace(stopping=threading.Event())
    assert agents.tool(team, "claude") == str(exe)
    assert agents.tool(team, "claude") == str(exe)  # the resolved path is kept

    monkeypatch.setattr(R.shutil, "which", lambda name: None)
    exe.unlink()
    with pytest.raises(RuntimeError, match="`claude` not found on PATH"):
        agents.tool(team, "claude")


class CoreCrashes(Fast):
    def develop(self, team, s):
        if s["assignee"] == "dev_pipeline":
            raise FileNotFoundError(2, "No such file or directory", "claude")
        return super().develop(team, s)


@pytest.mark.usefixtures("fast_poll")
def test_a_crash_blocks_only_that_task(repo):
    """2026-10-21: a crash blocks its task (it no longer pauses the developer mid-round)."""
    team, graph = make(repo, CoreCrashes())
    s = C.Continuous(team, graph).run()
    tasks = {t["id"]: t for t in team.load_backlog()}
    assert tasks["T-002"]["status"] == "done"  # dev_app kept going
    assert tasks["T-001"]["status"] == "blocked"
    assert tasks["T-001"]["blocked_reason"].startswith("CRASHED in develop at test_graph.py:")
    assert "'claude'" in tasks["T-001"]["blocked_reason"]
    assert "2 of its tasks in a row ended blocked (T-001, T-003)" in s["stop_reason"]
    assert s["in_flight"] == {}


# --- 2. a dev merge made before a crash is the orchestrator's, not the developer's ------------
class MergeThenCrash(Fast):
    """Round 1: dev moves meanwhile (a status commit). Round 2: develop() merges it, then the
    agent crashes; the resumed round must not count dev's backlog.yaml as the developer's."""

    crashed = False

    def plan(self, team, todo, feedback=None, devs=None):
        return [p for p in super().plan(team, todo, feedback, devs) if p["task_id"] == "T-001"]

    def develop(self, team, s):
        if s.get("rounds", 0) == 0:
            team.update_task("T-003", status="blocked", blocked_reason="by the owner")
            team.push_status("T-003", "backlog: T-003 blocked")
        elif not MergeThenCrash.crashed:
            MergeThenCrash.crashed = True
            raise RuntimeError("crash right after the dev merge")
        return super().develop(team, s)


@pytest.mark.usefixtures("fast_poll")
def test_a_dev_merge_before_a_crash_is_not_a_violation(repo):
    team, graph = make(repo, MergeThenCrash())
    graph.invoke({}, {"configurable": {"thread_id": "r"}})  # the crash blocks T-001
    wt = team.home / "worktrees" / "T-001"
    assert "Merge" in git(wt, "log", "-1", "--format=%s")  # the merge is kept
    t = next(t for t in team.load_backlog() if t["id"] == "T-001")
    assert t["status"] == "blocked" and "crash right after the dev merge" in t["blocked_reason"]
    assert not (team.home / "violations.jsonl").exists()


@pytest.mark.usefixtures("fast_poll")
def test_status_push_leaves_the_main_checkout_clean(repo):
    """Each status push fast-forwards the checkout to the commit just pushed (a push by URL
    left origin/dev behind: the checkout lagged one status with backlog.yaml modified)."""
    team, _ = make(repo, Fast())
    team.fake = False  # the checkout sync runs only for real teams
    team.backlog_path = repo / "devteam" / "backlog.yaml"
    for status in ("in_progress", "blocked", "todo"):
        team.update_task("T-001", status=status)
        assert team.push_status("T-001", f"backlog: T-001 {status}")
        assert git(repo, "status", "--porcelain") == ""
    assert "status: todo" in git(repo, "show", "dev:devteam/backlog.yaml")


BROKEN = b"\xdc2\x1e\x1e\xdc22\x1e\xdc\xdc\xc8\x1e"  # the first bytes of UX-dense's fixture


class BinaryFixture(FakeAgents):
    """dev_app commits a binary file; the judge sends the branch diff to the API as the real
    tester does, and its verdict carries "\\udcdc" (a \\udcxx escape in an agent's JSON)."""

    delay = 0

    def develop(self, team, s):
        if s["assignee"] == "dev_app":
            (Path(s["worktree"]) / "web" / "dense.color.u8").write_bytes(BROKEN)
        return super().develop(team, s)

    def judge(self, team, s, checks):
        base = f"{team.remote_name}/{team.cfg['base_branch']}"
        diff = team.git("diff", f"{base}...HEAD", cwd=s["worktree"])
        llm_call(team, "tester", s["task"]["id"], "sys", diff, {}, client=StubClient())
        v = super().judge(team, s, checks)
        return {**v, "summary": v["summary"] + " tester output \udcdc"}


def test_a_binary_file_in_the_diff_reaches_the_tester_and_the_packet(repo):
    team, graph = make(repo, BinaryFixture())
    graph.invoke({}, {"configurable": {"thread_id": "r"}})
    tasks = {t["id"]: t["status"] for t in team.load_backlog()}
    assert tasks["T-001"] == tasks["T-002"] == "done"
    for f in ("events.log", "packets/T-002.md"):  # strict UTF-8 reads
        assert "CRASHED" not in (team.home / f).read_text(encoding="utf-8")
    assert "tester output ?" in (team.home / "packets" / "T-002.md").read_text()


class TesterCrash(FakeAgents):
    """dev_app's tester step crashes exactly as UX-dense's did; dev_pipeline's task is fine."""

    delay = 0

    def judge(self, team, s, checks):
        if s["assignee"] == "dev_app":
            "tester output \udcdc".encode()  # UnicodeEncodeError: surrogates not allowed
        return super().judge(team, s, checks)


def test_a_crashing_task_is_blocked_and_the_other_task_finishes(repo):
    team, graph = make(repo, TesterCrash())
    config = {"configurable": {"thread_id": "r"}}
    graph.invoke({}, config)  # no exception: the run ends normally
    state = graph.get_state(config)
    assert not state.next  # nothing left to resume
    results = {r["id"]: r for r in state.values["results"]}
    assert results["T-001"]["status"] == "done"
    assert results["T-002"]["status"] == "blocked"
    assert results["T-002"]["reason"].startswith("CRASHED in test at test_graph.py:")
    assert "surrogates not allowed" in results["T-002"]["reason"]
    tasks = {t["id"]: t for t in team.load_backlog()}
    assert tasks["T-001"]["status"] == "done" and tasks["T-002"]["status"] == "blocked"
    assert tasks["T-002"]["blocked_reason"] == results["T-002"]["reason"]
    assert (team.home / "packets" / "T-002.md").exists()


def test_parallel_worktrees_do_not_race_on_git_locks(repo):
    """Seen in the first real v2 run: two `worktree add -b` at once -> .git/config lock."""
    team = team_for(repo)
    git(repo, "remote", "add", "devteam-fake", remote_of(repo))  # like origin: -b sets upstream
    team.fetch("dev")
    errors = []

    def add(name, start):
        start.wait()
        try:
            team.git(
                "worktree", "add", "-q", "-b", name, str(repo.parent / name), "devteam-fake/dev"
            )
        except RuntimeError as e:
            errors.append(str(e))

    for i in range(20):
        start = threading.Barrier(3)
        threads = [threading.Thread(target=add, args=(f"{x}{i}", start)) for x in "abc"]
        [t.start() for t in threads]
        [t.join() for t in threads]
    assert errors == []


def test_a_crashing_parallel_task_says_so_at_once(repo):
    class Crash(FakeAgents):
        delay = 0

        def develop(self, team, s):
            raise RuntimeError("codex failed: usage limit")

    team = team_for(repo)
    team.agents = Crash()
    graph = build_graph(team, SqliteSaver(sqlite3.connect(":memory:", check_same_thread=False)))
    graph.invoke({}, {"configurable": {"thread_id": "r"}})  # both tasks blocked, no raise
    log = (team.home / "events.log").read_text()
    assert "BLOCKED: CRASHED in develop at test_graph.py:" in log
    assert "codex failed: usage limit" in log
