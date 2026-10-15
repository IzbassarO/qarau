"""End-to-end run of the fake team on a throwaway repo, including crash + resume."""

import sqlite3
import subprocess
from pathlib import Path

from conftest import CFG, remote_of
from langgraph.checkpoint.sqlite import SqliteSaver

from devteam.agents import FakeAgents
from devteam.graph import Team, build_graph


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
            raise RuntimeError("usage limit")
        return super().judge(team, s, checks)


def test_fake_team_runs_parallel_loops_and_resumes(repo):
    team = Team(repo, CFG, {}, CrashOnce(), fake=True, remote=remote_of(repo))
    db = sqlite3.connect(team.home / "c.sqlite", check_same_thread=False)
    graph = build_graph(team, SqliteSaver(db))
    config = {"configurable": {"thread_id": "t1"}}

    try:
        graph.invoke({}, config)
        raise AssertionError("expected the crash")
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
