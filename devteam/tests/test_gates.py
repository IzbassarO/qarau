"""Automated gates instead of human review: CI, secrets, benchmark, conflicts, forbidden
actions, stop, release, daily report."""

import copy
import json
import shutil
import sqlite3
import threading
import time
from pathlib import Path

import pytest
from conftest import CFG, git, remote_of
from langgraph.checkpoint.sqlite import SqliteSaver

from devteam import costs
from devteam.agents import FakeAgents
from devteam.gates import FakeGitHub, GitHub, regressions, release, tag_release
from devteam.graph import Team, build_graph
from devteam.report import write_report

BENCH = """import json, pathlib, sys
out = pathlib.Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
pct = float(pathlib.Path("pipeline/score.txt").read_text())
v = {"registered_pct": pct, "models": 1, "reproj_px": 0.5, "points": 1000,
     "scale_err_cm": None, "time_s": 1}
(out / "results.json").write_text(json.dumps({"videos": {"room": v}}))
"""


def only_core(team, todo, feedback=None):
    return [{"task_id": "T-001", "assignee": "dev_pipeline", "plan": "p", "criteria": ["c"]}]


class Dev(FakeAgents):
    """Passes the tester at once; `change` decides what the developer writes."""

    delay = 0

    def __init__(self, change=None):
        self.change = change
        self.plan = only_core

    def develop(self, team, s):
        if self.change:
            self.change(Path(s["worktree"]), s)
        return {"session": "x", "summary": "did it"}

    def judge(self, team, s, checks):
        return {
            "passed": True,
            "feedback": "",
            "criteria": [{"criterion": "c", "met": True}],
            "summary": "s",
            "how_to_try": "t",
        }


def make(repo, agents, gh=None, **cfg_over):
    cfg = copy.deepcopy(CFG)
    cfg.update(cfg_over)
    team = Team(
        repo,
        cfg,
        {"ANTHROPIC_API_KEY": "sk-ant-test-0123456789abcdef"},
        agents,
        fake=True,
        remote=remote_of(repo),
    )
    if gh:
        team.gh = gh(team)
    graph = build_graph(team, SqliteSaver(sqlite3.connect(":memory:", check_same_thread=False)))
    return team, graph


def run(graph, tid="r"):
    graph.invoke({}, {"configurable": {"thread_id": tid}})


def task(team, tid="T-001"):
    return next(t for t in team.load_backlog() if t["id"] == tid)


def remote_files(repo, branch="dev"):
    return git(remote_of(repo), "ls-tree", "-r", "--name-only", branch).split()


def test_green_gates_merge_into_dev_with_the_packet_as_pr_body(repo):
    team, graph = make(repo, Dev(lambda wt, s: (wt / "pipeline" / "b.py").write_text("b = 1\n")))
    run(graph)
    t = task(team)
    assert t["status"] == "done" and t["pr"] == "fake://pr/1"
    assert "pipeline/b.py" in remote_files(repo)
    body = team.gh.prs[1]["body"]
    assert "secret scan (gitleaks): clean" in body and "## What changed" in body
    assert "main" not in [p["base"] for p in team.gh.prs.values()]


def test_red_ci_sends_back_then_blocks(repo):
    team, graph = make(
        repo,
        Dev(lambda wt, s: (wt / "pipeline" / "b.py").write_text("x\n")),
        gh=lambda t: FakeGitHub(t, ci_ok=False),
        max_fix_loops=1,
    )
    run(graph)
    t = task(team)
    assert t["status"] == "blocked" and "GitHub CI failed" in t["blocked_reason"]
    assert team.gh.merged == [] and "pipeline/b.py" not in remote_files(repo)


def test_secret_is_never_committed(repo):
    def leak(wt, s):
        (wt / "pipeline" / "a.py").write_text('aws = "AKIAIOSFODNN7EXAMPLQ"\n')  # gitleaks:allow
        (wt / "pipeline" / "c.py").write_text('key = "sk-ant-test-0123456789abcdef"\n')  # .env
        (wt / "pipeline" / "ok.py").write_text("ok = 1\n")

    team, graph = make(repo, Dev(leak))
    run(graph)
    log = git(repo, "log", "-p", team.branch_for("T-001"))
    assert "AKIAIOSFODNN7EXAMPLQ" not in log and "0123456789abcdef" not in log  # gitleaks:allow
    assert "pipeline/ok.py" in remote_files(repo)  # the clean part still lands
    events = (team.home / "events.log").read_text()
    assert "secret scan: refused" in events and "0123456789abcdef" not in events


def bench_repo(repo, score):
    git(repo, "pull", "-q", "--no-rebase", remote_of(repo), "dev")
    (repo / "pipeline" / "bench.py").write_text(BENCH)
    (repo / "pipeline" / "score.txt").write_text(score)
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "bench")
    git(repo, "push", "-q", remote_of(repo), "dev")
    bench = copy.deepcopy(CFG["gates"])
    bench["benchmark"]["command"] = "python3 pipeline/bench.py {out}"
    return bench


def test_benchmark_regression_sends_back(repo):
    gates = bench_repo(repo, "90")
    team, graph = make(
        repo,
        Dev(lambda wt, s: (wt / "pipeline/score.txt").write_text("80")),
        gates=gates,
        max_fix_loops=0,
    )
    run(graph)
    t = task(team)
    assert t["status"] == "blocked" and "registered 90.0% -> 80.0%" in t["blocked_reason"]


def test_benchmark_no_regression_merges_and_records_table(repo):
    gates = bench_repo(repo, "90")
    team, graph = make(
        repo, Dev(lambda wt, s: (wt / "pipeline/score.txt").write_text("91")), gates=gates
    )
    run(graph)
    assert task(team)["status"] == "done"
    assert "| room | 91.0 |" in team.gh.prs[1]["body"]


def test_benchmark_that_cannot_run_on_dev_blocks_instead_of_skipping(repo):
    """The first real v2 run merged P2a unmeasured: dev's run failed and was 'skipped'."""
    gates = copy.deepcopy(CFG["gates"])
    gates["benchmark"]["command"] = "false"
    team, graph = make(
        repo, Dev(lambda wt, s: (wt / "pipeline" / "b.py").write_text("x\n")), gates=gates
    )
    run(graph)
    t = task(team)
    assert t["status"] == "blocked" and "benchmark could not run on `dev`" in t["blocked_reason"]
    assert team.gh.prs == {}  # nothing pushed or merged


def test_video_failing_on_dev_is_named_and_others_still_gate(repo):
    gates = bench_repo(repo, "90")
    flaky = BENCH.replace(
        '"videos": {"room": v}', '"videos": {"room": v}, "errors": {"mykolaiv": "OverflowError"}'
    )
    (repo / "pipeline" / "bench.py").write_text(flaky)  # local only: dev on the remote stays
    team, graph = make(
        repo, Dev(lambda wt, s: (wt / "pipeline/score.txt").write_text("91")), gates=gates
    )
    run(graph)
    assert task(team)["status"] == "done"
    body = team.gh.prs[1]["body"]
    assert "| room | 91.0 |" in body and "not gated (COLMAP noise" in body


def test_regression_rules():
    base = {
        "videos": {
            "v": {
                "registered_pct": 90,
                "models": 1,
                "reproj_px": 0.5,
                "points": 1000,
                "scale_err_cm": 1.0,
            }
        }
    }
    same = copy.deepcopy(base)
    assert regressions(base, same, CFG["gates"]["benchmark"]) == []
    worse = copy.deepcopy(base)
    worse["videos"]["v"].update(models=2, reproj_px=0.6, points=800, scale_err_cm=2.0)
    assert len(regressions(base, worse, CFG["gates"]["benchmark"])) == 4
    assert regressions(base, {"videos": {}}, CFG["gates"]["benchmark"]) == [
        "v: missing from the new run"
    ]


def test_conflict_with_dev_blocks_without_rewriting_history(repo):
    def change(wt, s):
        (wt / "pipeline" / "a.py").write_text("a = 'mine'\n")
        # meanwhile someone else lands a conflicting change on dev
        other = repo.parent / "other"
        if not other.exists():
            git(repo.parent, "clone", "-q", "-b", "dev", remote_of(repo), str(other))
            (other / "pipeline" / "a.py").write_text("a = 'theirs'\n")
            git(other, "commit", "-qam", "theirs")
            git(other, "push", "-q", "origin", "dev")

    team, graph = make(repo, Dev(change))
    run(graph)
    t = task(team)
    assert t["status"] == "blocked" and "conflicts with dev" in t["blocked_reason"]
    assert team.gh.prs == {}  # nothing pushed or opened


def test_forbidden_git_and_gh_actions(repo):
    team, _ = make(repo, Dev())
    url = remote_of(repo)
    for args in [
        ("push", url, "dev"),
        ("push", "--force", url, "devteam-fake/T-001"),
        ("push", url, "+devteam-fake/T-001"),
        ("push", url, ":devteam-fake/T-001"),
        ("push", "--delete", url, "devteam-fake/T-001"),
        ("push", url, "main"),
        ("branch", "-D", "main"),
        ("rebase", "dev"),
        ("update-ref", "refs/heads/dev", "HEAD"),
        ("push", url, "refs/tags/v1"),
        ("push", url, "+refs/tags/release-2026-10-15"),
        ("push", "--force", url, "refs/tags/release-2026-10-15"),
        ("push", "--tags", url),
    ]:
        with pytest.raises(PermissionError):
            team.git(*args)
    team._guard(("push", "-q", url, "refs/tags/release-2026-10-15.2"))  # the one tag allowed
    gh = GitHub(team)
    for args in [
        ("api", "repos/x/y/branches/main/protection", "-X", "DELETE"),
        ("pr", "merge", "1", "--admin"),
        ("pr", "merge", "1", "--delete-branch"),
        ("repo", "delete", "x/y"),
        ("workflow", "disable", "ci"),
    ]:
        with pytest.raises(PermissionError):
            gh.gh(*args)


def test_stop_kills_running_children(repo):
    team, _ = make(repo, Dev())
    res = {}
    th = threading.Thread(target=lambda: res.update(team.run_proc("sleep 30", str(repo), 60)))
    th.start()
    time.sleep(0.5)
    t0 = time.time()
    team.kill_all()
    th.join(5)
    assert not th.is_alive() and time.time() - t0 < 5 and res["ok"] is False


def test_release_merges_dev_into_main_only_with_a_green_benchmark(repo):
    team, graph = make(repo, Dev(lambda wt, s: (wt / "pipeline" / "b.py").write_text("x\n")))
    run(graph)
    shutil.rmtree(team.home / "bench")  # the gate's run is cached per pipeline tree
    team.cfg["gates"]["benchmark"]["command"] = "false"
    assert release(team).startswith("release blocked: the full benchmark failed")
    assert "pipeline/b.py" not in remote_files(repo, "main")

    team.cfg["gates"] = bench_repo(repo, "90")
    day = time.strftime("%Y-%m-%d")
    assert release(team).endswith(f"(tag release-{day})")
    assert "pipeline/b.py" in remote_files(repo, "main")
    assert release(team) == "release: nothing new on dev"
    remote = repo.parent / "remote.git"
    main_sha = git(remote, "rev-parse", "main").strip()
    assert git(remote, "rev-parse", f"release-{day}^{{commit}}").strip() == main_sha
    assert tag_release(team, "re-run") == f"release-{day}"  # same commit: no new tag

    (repo / "pipeline" / "c.py").write_text("c = 1\n")  # a second release the same day
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "more")
    git(repo, "push", "-q", str(remote), "dev")
    assert release(team).endswith(f"(tag release-{day}.2)")
    assert git(remote, "rev-parse", f"release-{day}^{{commit}}").strip() == main_sha  # unmoved


def test_daily_report(repo):
    team, graph = make(repo, Dev(lambda wt, s: (wt / "pipeline" / "b.py").write_text("x\n")))
    costs.record(team.home, "brain", "claude-opus-5-5", "T-001", 100, 50, 0.12, True)
    run(graph)
    text = write_report(team).read_text()
    assert "T-001" in text and "fake://pr/1" in text
    assert "| brain | claude-opus-5-5 | 1 | 0.120 | API |" in text
    assert "## Blocked" in text and "## Benchmark" in text
    json.dumps(text)  # plain text, no secrets objects
