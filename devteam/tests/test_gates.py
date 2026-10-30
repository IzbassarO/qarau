"""Automated gates instead of human review: CI, secrets, benchmark, conflicts, forbidden
actions, stop, release, daily report, the e2e gate, the test-weakening guard, criteria numbers."""

import copy
import json
import shutil
import socket
import sqlite3
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import anthropic
import httpx
import pytest
import yaml
from conftest import CFG, HERE, git, remote_of, team_for
from langgraph.checkpoint.sqlite import SqliteSaver
from PIL import Image

from devteam import costs
from devteam import gates as G
from devteam import graph as GR
from devteam import real as R
from devteam.agents import FakeAgents
from devteam.gates import FakeGitHub, GitHub, regressions, release, tag_release
from devteam.graph import Team, build_graph, criterion_key
from devteam.real import e2e_rule, quick_benchmark
from devteam.report import write_report, write_reports

BENCH = """import json, pathlib, sys
out = pathlib.Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
pct = float(pathlib.Path("pipeline/score.txt").read_text())
v = {"registered_pct": pct, "models": 1, "reproj_px": 0.5, "points": 1000,
     "scale_err_cm": None, "time_s": 1}
(out / "results.json").write_text(json.dumps({"videos": {"room": v}}))
"""


def only_core(team, todo, feedback=None, devs=None):
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


class Fast(FakeAgents):
    delay = 0


def make_team(repo, agents=None, fake=True, **cfg_over):
    cfg = copy.deepcopy(CFG)
    cfg.update(cfg_over)
    team = Team(repo, cfg, {}, agents or Fast(), fake=fake, remote=remote_of(repo))
    graph = build_graph(team, SqliteSaver(sqlite3.connect(":memory:", check_same_thread=False)))
    return team, graph


def only(tid, dev):
    def plan(team, todo, feedback=None, devs=None):
        return [{"task_id": tid, "assignee": dev, "plan": "p", "criteria": ["stub file exists"]}]

    return plan


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


def test_ci_failure_log_reaches_the_packet_and_the_developer(repo, monkeypatch):
    url = "https://github.com/o/r/actions/runs/39008036568/job/117324416132"
    log = "".join(f"web\tstep\t\x1b[31mline {i}\x1b[39m\n" for i in range(200))
    calls = []

    def gh(self, *args):
        calls.append(args)
        if args[:2] == ("run", "view"):
            return log
        return json.dumps(
            {
                "headRefOid": self.sha,
                "statusCheckRollup": [
                    {
                        "name": "web",
                        "status": "COMPLETED",
                        "conclusion": "FAILURE",
                        "detailsUrl": url,
                    },
                    {
                        "name": "web-e2e",
                        "status": "COMPLETED",
                        "conclusion": "FAILURE",
                        "detailsUrl": url,
                    },
                    {"name": "backend", "status": "COMPLETED", "conclusion": "SUCCESS"},
                ],
            }
        )

    monkeypatch.setattr(GitHub, "gh", gh)
    real = GitHub(None)
    real.sha = "abc"
    ok, why = real.wait_ci(25, "abc", 60)
    assert not ok and why.startswith(f"web: FAILURE {url}")
    assert [c for c in calls if c[0] == "run"] == [("run", "view", "39008036568", "--log-failed")]
    assert "line 50\n" in why and "line 199" in why and "line 49\n" not in why
    assert "\x1b" not in why

    seen = []

    class Red(FakeGitHub):
        def wait_ci(self, number, sha, timeout_s):
            return False, f"web: FAILURE {url}\n```\nReferenceError: ResizeObserver\n```"

    team, graph = make(
        repo,
        Dev(lambda wt, s: seen.append(s.get("feedback", ""))),
        gh=Red,
        max_fix_loops=1,
    )
    run(graph)
    assert "ResizeObserver" in seen[-1]  # round 2 is told the error, not only a link
    assert "ResizeObserver" in (team.home / "packets" / "T-001.md").read_text()


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


def test_two_tasks_benchmarking_one_commit_take_turns_and_measure_it_once(repo, tmp_path):
    """dev_pipeline and dev_core (2026-10-30) both measured the dev baseline at once: the first
    one's cleanup removed bench-wt/<sha> under the second, which crashed."""
    gates = bench_repo(repo, "90")
    calls = tmp_path / "calls"
    gates["benchmark"]["command"] = f"echo x >> {calls}; sleep 1; python3 pipeline/bench.py {{out}}"
    team, _ = make(repo, Dev(), gates=gates)
    sha = git(repo, "rev-parse", "HEAD").strip()
    got, errors = [], []

    def measure():
        try:
            got.append(G.benchmark(team, sha))
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=measure) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert [r["videos"]["room"]["registered_pct"] for r in got] == [90.0, 90.0]
    assert calls.read_text().count("x") == 1  # the second read the first one's cache


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


NOISY = """import json, pathlib, sys
out = pathlib.Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
calls = pathlib.Path(sys.argv[2]); k = int(calls.read_text()) if calls.exists() else 0
calls.write_text(str(k + 1))
with open(sys.argv[2] + ".argv", "a") as f: f.write(" ".join(sys.argv[3:]) + "\\n")
vals = pathlib.Path("pipeline/score.txt").read_text().split()  # one per call, the last repeats
v = {"registered_pct": float(vals[min(k, len(vals) - 1)]), "models": 1, "reproj_px": 0.5,
     "points": 1000, "scale_err_cm": None, "time_s": 1}
(out / "results.json").write_text(json.dumps({"videos": {"room": v}}))
"""


def noisy_repo(repo, tmp_path):
    """Call 1 measures dev (score 90); call k takes the k-th value of the branch's score.txt,
    so "x 80 91" is one noisy run on the branch, then a good one."""
    gates = bench_repo(repo, "90")
    (repo / "pipeline" / "bench.py").write_text(NOISY)
    git(repo, "commit", "-qam", "noisy bench")
    git(repo, "push", "-q", remote_of(repo), "dev")
    gates["benchmark"]["command"] = f"python3 pipeline/bench.py {{out}} {tmp_path / 'calls'}"
    return gates


def head_of(team, ref="devteam-fake/T-001"):
    return team.git("rev-parse", ref).strip()


def test_benchmark_regression_that_does_not_repeat_merges_showing_both_attempts(repo, tmp_path):
    gates = noisy_repo(repo, tmp_path)
    team, graph = make(
        repo, Dev(lambda wt, s: (wt / "pipeline/score.txt").write_text("x 80 91")), gates=gates
    )
    run(graph)
    assert task(team)["status"] == "done"
    body = team.gh.prs[1]["body"]
    assert "benchmark attempt 1: regression vs dev" in body and "| room | 80.0 |" in body
    assert "benchmark attempt 2, only room: no regression" in body and "| room | 91.0 |" in body
    assert (tmp_path / "calls.argv").read_text().splitlines() == ["", "", "--only room"]
    cached = G.cached_benchmark(team, head_of(team))  # final: with the second numbers
    assert cached["videos"]["room"]["registered_pct"] == 91


def test_benchmark_regression_that_repeats_sends_back_and_is_never_final(repo, tmp_path):
    gates = noisy_repo(repo, tmp_path)
    team, graph = make(
        repo,
        Dev(lambda wt, s: (wt / "pipeline/score.txt").write_text("x 80 80")),
        gates=gates,
        max_fix_loops=0,
    )
    run(graph)
    t = task(team)
    assert t["status"] == "blocked" and "repeated on a second run" in t["blocked_reason"]
    assert "registered 90.0% -> 80.0%" in t["blocked_reason"]
    packet = (team.home / "packets" / "T-001.md").read_text()
    assert "benchmark attempt 1" in packet and "the regression repeated" in packet
    head = head_of(team)
    assert (team.home / "bench" / G.bench_key(team, head) / G.NOT_FINAL).exists()
    assert G.cached_benchmark(team, head) is None  # the next gate on this tree measures again


def test_a_failed_gated_video_is_never_the_final_result(repo):
    team, _ = make(repo, Dev(), gates=bench_repo(repo, "90"))
    sha = git(repo, "rev-parse", "HEAD").strip()
    out = team.home / "bench" / G.bench_key(team, sha)
    out.mkdir(parents=True)
    ok = {"registered_pct": 90, "models": 1, "reproj_px": 0.5, "points": 1, "time_s": 1}
    res = {"videos": {"room": ok}, "errors": {"notre-dame": "COLMAP crashed"}}
    (out / "results.json").write_text(json.dumps(res))
    assert G.cached_benchmark(team, sha) is None
    res["errors"] = {"mykolaiv": "COLMAP crashed"}  # not gated (config per_video)
    (out / "results.json").write_text(json.dumps(res))
    assert G.cached_benchmark(team, sha)["videos"]["room"] == ok
    team.cfg["gates"]["benchmark"]["command"] = "true"  # a run that writes nothing
    (out / G.NOT_FINAL).write_text("room")
    assert G.benchmark(team, sha) is None  # never the stale results.json
    assert not (out / G.NOT_FINAL).exists() and not (out / "results.json").exists()


def test_cosmetic_only_diffs():
    def d(minus, plus):
        return "\n".join(["--- a/x.py", "+++ b/x.py", "@@ -1 +1 @@", *minus, *plus])

    assert G.cosmetic_only("")
    assert G.cosmetic_only(d(["-# old note"], ["+# new note", "+", "+// js note"]))
    assert G.cosmetic_only(d(["-x = f(a,  b)"], ["+x = f(a, b)  "]))
    assert not G.cosmetic_only(d(["-x = 1"], ["+x = 2"]))
    assert not G.cosmetic_only(d(["-    y()"], ["+y()"]))  # indentation is code in Python
    assert not G.cosmetic_only(d(["-a()", "-b()"], ["+b()", "+a()"]))  # so is the order


def test_packet_warns_when_only_comments_changed_after_a_benchmark_sendback(repo):
    def change(wt, s):
        if not s.get("rounds"):
            (wt / "pipeline/score.txt").write_text("80")
        else:  # round 2: a comment, nothing else
            (wt / "pipeline/a.py").write_text("a = 1\n# measured again\n")

    team, graph = make(repo, Dev(change), gates=bench_repo(repo, "90"), max_fix_loops=1)
    run(graph)
    assert task(team)["status"] == "blocked"
    packet = (team.home / "packets" / "T-001.md").read_text()
    assert "WARNING: since the benchmark sent this task back, pipeline/ changed only" in packet
    assert "since the benchmark sent this task back" in (team.home / "events.log").read_text()


def test_no_warning_when_the_code_changed_after_a_benchmark_sendback(repo):
    def change(wt, s):
        (wt / "pipeline/score.txt").write_text("80" if not s.get("rounds") else "91")

    team, graph = make(repo, Dev(change), gates=bench_repo(repo, "90"), max_fix_loops=1)
    run(graph)
    assert task(team)["status"] == "done"
    assert "WARNING" not in team.gh.prs[1]["body"]


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
    assert (
        "| room | 91.0 |" in body
        and "not gated (edited news clip, no one honest model (P3c)" in body
    )


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


def theirs_lands_on_dev(repo):
    """Someone else lands a conflicting change on dev while the task works."""
    other = repo.parent / "other"
    if not other.exists():
        git(repo.parent, "clone", "-q", "-b", "dev", remote_of(repo), str(other))
        (other / "pipeline" / "a.py").write_text("a = 'theirs'\n")
        git(other, "commit", "-qam", "theirs")
        git(other, "push", "-q", "origin", "dev")


def test_conflict_with_dev_gets_a_resolve_step_then_merges_without_rewriting_history(repo):
    """Owner, 2026-10-17: the orchestrator merges dev in before a round; on conflicts the
    developer gets a short step with the file list (not a full round), then work goes on."""
    seen = []

    def change(wt, s):
        seen.append(s.get("conflicts"))
        if s.get("conflicts"):  # the resolve step: markers in, resolution out
            assert "<<<<<<<" in (wt / "pipeline" / "a.py").read_text()
            (wt / "pipeline" / "a.py").write_text("a = 'both'\n")
            (wt / "web" / "app.ts").write_text("not part of resolving\n")  # reverted
            return
        if len(seen) == 1:
            (wt / "pipeline" / "a.py").write_text("a = 'mine'\n")
            theirs_lands_on_dev(repo)

    team, graph = make(repo, Dev(change))
    run(graph)
    assert task(team)["status"] == "done"
    assert seen == [None, ["pipeline/a.py"], None]  # round, resolve step, round
    url = remote_of(repo)
    assert git(url, "show", "dev:pipeline/a.py") == "a = 'both'\n"
    assert git(url, "show", "dev:web/app.ts") == "export {}\n"
    theirs = git(repo.parent / "other", "rev-parse", "HEAD").strip()
    git(url, "merge-base", "--is-ancestor", theirs, "dev")  # merged in, never rebased away


def test_conflict_markers_left_block_the_task(repo):
    def change(wt, s):
        if not s.get("conflicts"):
            (wt / "pipeline" / "a.py").write_text("a = 'mine'\n")
            theirs_lands_on_dev(repo)

    team, graph = make(repo, Dev(change))
    run(graph)
    t = task(team)
    assert (
        t["status"] == "blocked" and "could not resolve conflicts with dev" in t["blocked_reason"]
    )
    assert "markers left in ['pipeline/a.py']" in t["blocked_reason"]
    wt = Path(team.home / "worktrees" / "T-001")
    assert not (wt / ".git").is_file() or "MERGING" not in git(wt, "status")  # merge aborted
    assert team.gh.prs == {}  # nothing pushed
    assert "theirs" not in git(repo, "log", "--format=%s", team.branch_for("T-001"))


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


def billed_at(home, when: float, usd: float, agent="tester"):
    """A settled API call at `when` (epoch), as costs.record would have written it then."""
    db = costs._db(home)
    day, ts = (
        time.strftime("%Y-%m-%d", time.localtime(when)),
        time.strftime("%H:%M:%S", time.localtime(when)),
    )
    db.execute(
        "insert into usage (day, ts, agent, model, task, tokens_in, tokens_out, usd, billed) "
        "values (?,?,?,'m','T-001',1,1,?,1)",
        (day, ts, agent, usd),
    )
    db.close()


def test_budget_counts_the_last_24_hours_not_the_calendar_day(tmp_path):
    """Owner, 2026-10-21: the cap reset at midnight, so a night run could spend it twice."""
    now = time.time()
    billed_at(tmp_path, now - 23 * 3600, 2.0)  # yesterday evening, still inside the window
    billed_at(tmp_path, now - 25 * 3600, 5.0)  # outside
    assert costs.spent_24h(tmp_path) == pytest.approx(2.0)
    with pytest.raises(costs.BudgetExceeded, match="last 24 h"):
        costs.reserve(tmp_path, 3.0, "tester", "m", "T-001", 1.5)
    costs.reserve(tmp_path, 3.6, "tester", "m", "T-001", 1.5)
    assert costs.spent_24h(tmp_path, now=now + 25 * 3600) == 0  # a day later all left it


def test_a_run_past_midnight_writes_the_report_of_both_days(repo):
    team, _ = make(repo, Dev())
    now = time.time()
    yesterday = time.strftime("%Y-%m-%d", time.localtime(now - 86400))
    billed_at(team.home, now - 86400, 0.25, agent="brain")
    run = {"run": "cont-x", "started": f"{yesterday}T23:00:00", "stop_reason": "max_hours",
           "ended": time.strftime("%Y-%m-%dT%H:%M:%S"), "tasks": [], "spend": []}  # fmt: skip
    (team.home / "runs").mkdir()
    (team.home / "runs" / "cont-x.json").write_text(json.dumps(run))
    today = write_reports(team, now - 86400)
    old = today.with_name(f"{yesterday}.md")
    assert today.name == time.strftime("%Y-%m-%d.md") and old.exists()
    assert "| brain | m | 1 | 0.250 | API |" in old.read_text()
    assert "| brain |" not in today.read_text()  # each day's own costs
    for path in (old, today):  # the run itself is in both
        assert "Kerege continuous run cont-x" in path.read_text()


# --- 2. e2e gate --------------------------------------------------------------------------------
E2E = (
    "{py} -c \"import os; open({log!r}, 'w').write(os.getcwd() + ' {port}')\" && "
    "({py} -m http.server {port} --bind 127.0.0.1 >/dev/null 2>&1 &) && sleep 1 && "
    "echo '  ✓  1 [chromium] › e2e/far-layer.spec.ts:9:1 › malformed far arrays warn (2.0s)' && "
    "echo '  -  2 [chromium] › e2e/viewer.spec.ts:5:1 › real bundle (skipped)' && "
    "echo '  1 passed, 1 skipped' && exit {code}"
)


class Judged(Fast):
    """Counts the LLM tester calls."""

    judged = 0
    feedback = ""
    checks: list = []

    def judge(self, team, s, checks):
        self.judged += 1
        self.checks = checks
        return super().judge(team, s, checks)

    def develop(self, team, s):
        self.feedback = s.get("feedback") or self.feedback
        return super().develop(team, s)


def e2e_team(repo, tmp_path, code=0, dev="dev_app", agents=None, loops=0, command=None, mins=None):
    agents = agents or Fast()
    agents.plan = only("T-002" if dev == "dev_app" else "T-001", dev)
    devs = copy.deepcopy(CFG["developers"])
    for d in devs.values():
        d.update(setup=[], checks=[])
        d.pop("screenshots", None)
    gates = copy.deepcopy(CFG["gates"])
    log = tmp_path / "e2e.log"
    gates["e2e"]["command"] = command or E2E.format(
        py=sys.executable, log=str(log), port="{port}", code=code
    )
    gates["e2e"]["timeout_min"] = mins or gates["e2e"]["timeout_min"]
    team, graph = make_team(
        repo, agents, fake=False, developers=devs, gates=gates, max_fix_loops=loops
    )
    graph.invoke({}, {"configurable": {"thread_id": "r"}})
    return team, log


def port_open(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


@pytest.mark.usefixtures("fast_poll")
def test_e2e_runs_on_the_clean_checkout_with_a_fresh_server_stopped_after(repo, tmp_path):
    team, log = e2e_team(repo, tmp_path)
    cwd, port = log.read_text().rsplit(" ", 1)
    assert Path(cwd).resolve() == (team.home / "check-wt" / "T-002").resolve()
    assert int(port) > 0 and not port_open(int(port))  # the server it left was killed
    packet = (team.home / "packets" / "T-002.md").read_text()
    assert "- ok: `" in packet and "http.server" in packet
    events = (team.home / "events.log").read_text()
    assert "e2e (Playwright) on the clean checkout" in events
    assert f"left port {port} listening" in events  # reported, as for any step


@pytest.mark.usefixtures("fast_poll")
def test_failed_e2e_sends_the_task_back_with_each_test_line(repo, tmp_path):
    team, _ = e2e_team(repo, tmp_path, code=1)
    t = task(team, "T-002")
    assert t["status"] == "blocked"
    packet = (team.home / "packets" / "T-002.md").read_text()
    assert "- FAIL: `" in packet and "http.server" in packet


@pytest.mark.usefixtures("fast_poll")
def test_failed_e2e_goes_back_without_calling_the_llm_tester(repo, tmp_path):
    agents = Judged()
    team, _ = e2e_team(repo, tmp_path, code=1, agents=agents, loops=1)
    assert agents.judged == 0  # neither round: the e2e failed both times
    assert "e2e failed: sent back without the LLM tester" in (team.home / "events.log").read_text()
    reason = task(team, "T-002")["blocked_reason"]
    assert "Kerege ran the e2e tests (Playwright) right after your round" in reason
    assert "Failing checks:" in agents.feedback and "1 passed, 1 skipped" in agents.feedback
    assert "screenshots missing" not in reason


@pytest.mark.usefixtures("fast_poll")
def test_passing_e2e_still_goes_to_the_llm_tester(repo, tmp_path):
    agents = Judged()
    e2e_team(repo, tmp_path, agents=agents)
    assert agents.judged == 1


@pytest.mark.usefixtures("fast_poll")
def test_e2e_runs_only_when_the_branch_changes_web(repo, tmp_path):
    team, log = e2e_team(repo, tmp_path, dev="dev_pipeline")
    assert not log.exists()
    assert "e2e (Playwright)" not in (team.home / "events.log").read_text()


def test_judge_sees_which_e2e_tests_ran_or_skipped():
    out = "vite log\n" * 500 + "  ✓  1 [chromium] › a.spec.ts:1:1 › far (1s)\n" * 3
    out += "  -  4 [chromium] › b.spec.ts:1:1 › real data\n  3 passed, 1 skipped\n"
    shown = GR.e2e_output(out)
    assert "› far" in shown and "› real data" in shown and "3 passed, 1 skipped" in shown
    assert shown.count("vite log") <= 40


# Playwright's list reporter, as Kerege saw it on DS1 (2026-10-26), trimmed
E2E_TIMED_OUT = """Running 208 tests using 4 workers
vite log line
  ✓   1 [desktop] › e2e/login.spec.ts:13:7 › signed-out login: light, en (2.1s)
  ✓   2 [desktop] › e2e/login.spec.ts:13:7 › signed-out login: light, ru (2.0s)
  -   3 [desktop] › e2e/viewer.spec.ts:19:3 › viewer: notre-dame
  ✘   4 [phone] › e2e/viewer-chrome.spec.ts:21:5 › v8 viewer chrome fits: light, kk (3.0m)
"""
E2E_FAILED = """Running 3 tests using 2 workers
  ✓   1 [desktop] › e2e/login.spec.ts:13:7 › signed-out login: light, en (2.1s)
  ✘   2 [desktop] › e2e/point-info.spec.ts:90:1 › click while drag still orbits (6.1s)
  ✘   3 [phone] › e2e/point-info.spec.ts:90:1 › click while drag still orbits (6.3s)

  1) [desktop] › e2e/point-info.spec.ts:90:1 › click while drag still orbits ──────────────

    Error: expect(locator).toHaveAttribute(expected) failed

    Locator: getByRole('button', { name: 'Point info' })
    Expected: "false"
    Timeout: 5000ms
    Error: element(s) not found

      108 |   await expect(panel).toHaveCount(0);
    > 109 |   await expect(tool).toHaveAttribute('aria-pressed', 'false');
      110 |   expect(errors).toEqual([]);
      111 | });

  2) [phone] › e2e/point-info.spec.ts:90:1 › click while drag still orbits ────────────────

    Error: page.screenshot: Test timeout of 180000ms exceeded.

  2 failed
    [desktop] › e2e/point-info.spec.ts:90:1 › click while drag still orbits
    [phone] › e2e/point-info.spec.ts:90:1 › click while drag still orbits
  1 passed (9.0m)
"""


def test_e2e_report_says_timeout_plainly_with_counts():
    assert GR.e2e_report(E2E_TIMED_OUT, True, 20) == (
        "TIMEOUT: e2e stopped after 20 min (not a test failure); 2 passed, 1 failed, "
        "204 not run.\n"
        "✘   4 [phone] › e2e/viewer-chrome.spec.ts:21:5 › v8 viewer chrome fits: light, kk (3.0m)"
    )


def test_e2e_report_gives_each_failing_test_and_its_first_error_lines():
    assert GR.e2e_report(E2E_FAILED, False, 20) == (
        "e2e FAILED: 2 failed, 1 passed.\n"
        "1) [desktop] › e2e/point-info.spec.ts:90:1 › click while drag still orbits\n"
        "  Error: expect(locator).toHaveAttribute(expected) failed\n"
        "  Locator: getByRole('button', { name: 'Point info' })\n"
        '  Expected: "false"\n'
        "  Timeout: 5000ms\n"
        "  Error: element(s) not found\n"
        "  108 |   await expect(panel).toHaveCount(0);\n"
        "  > 109 |   await expect(tool).toHaveAttribute('aria-pressed', 'false');\n"
        "  110 |   expect(errors).toEqual([]);\n\n"
        "2) [phone] › e2e/point-info.spec.ts:90:1 › click while drag still orbits\n"
        "  Error: page.screenshot: Test timeout of 180000ms exceeded."
    )


# --retries=1 (owner, 2026-10-27): the list reporter prints every attempt
E2E_RETRIED = """Running 3 tests using 3 workers
  ✘  1 [phone] › e2e/viewer.spec.ts:19:3 › viewer: notre-dame (3.0m)
  ✓  2 [phone] › e2e/viewer.spec.ts:19:3 › viewer: notre-dame (retry #1) (40.1s)
  ✘  3 [desktop] › e2e/evidence.spec.ts:5:1 › panel stays ready (6.1s)
  ✘  4 [desktop] › e2e/evidence.spec.ts:5:1 › panel stays ready (retry #1) (6.0s)
  ✓  5 [desktop] › e2e/login.spec.ts:13:7 › signed-out login (2.1s)

  1) [desktop] › e2e/evidence.spec.ts:5:1 › panel stays ready ───────────────────────

    Error: expect(locator).toBeVisible() failed

    Error Context: test-results/pw/evidence-desktop/error-context.md

    Retry #1 ──────────────────────────────────────────────────────────────────────────

    Error: expect(locator).toBeVisible() failed

    Error Context: test-results/pw/evidence-desktop-retry1/error-context.md

  2) [phone] › e2e/viewer.spec.ts:19:3 › viewer: notre-dame ─────────────────────────

    Test timeout of 180000ms exceeded.

    Error Context: ../.env

  1 failed
    [desktop] › e2e/evidence.spec.ts:5:1 › panel stays ready ───────────────────────
  1 flaky
    [phone] › e2e/viewer.spec.ts:19:3 › viewer: notre-dame ─────────────────────────
  1 passed (4.1m)
"""


def test_e2e_command_runs_three_workers_and_one_retry():
    cfg = yaml.safe_load((HERE / "config.yaml").read_text())["gates"]["e2e"]
    assert "--workers=3 --retries=1" in cfg["command"]


def test_flaky_test_is_listed_not_counted_as_failed():
    assert GR.e2e_flaky(E2E_RETRIED) == ["[phone] › e2e/viewer.spec.ts:19:3 › viewer: notre-dame"]
    report = GR.e2e_report(E2E_RETRIED, False, 30)
    assert report.startswith(
        "e2e FAILED: 1 failed, 1 passed. Flaky (failed, then passed on retry; not blocking): 1.\n"
        "1) [desktop] › e2e/evidence.spec.ts:5:1 › panel stays ready\n"
    )
    assert "Test timeout" not in report  # the flaky one's error is not a failure to fix
    assert report.endswith(
        "Flaky (not blocking, fix when you can):\n"
        "[phone] › e2e/viewer.spec.ts:19:3 › viewer: notre-dame"
    )


def test_failed_test_gets_the_head_of_its_error_context(tmp_path):
    ctx = tmp_path / "web" / "test-results" / "pw" / "evidence-desktop"
    ctx.mkdir(parents=True)
    (ctx / "error-context.md").write_text("".join(f"line {i}\n" for i in range(1, 50)))
    (tmp_path / ".env").write_text("SECRET=1\n")
    report = GR.e2e_report(E2E_RETRIED, False, 30, tmp_path / "web" / "x" / "y")
    assert "error-context.md" not in report  # not there: nothing, no crash
    report = GR.e2e_report(E2E_RETRIED, False, 30, tmp_path / "web")
    assert "  error-context.md (first 30 lines):\n    line 1\n" in report
    assert "    line 30\n" in report and "line 31" not in report
    assert "SECRET" not in report  # a path out of the checkout is never read


@pytest.mark.usefixtures("fast_poll")
def test_flaky_e2e_does_not_block_and_is_in_packet_and_daily_report(repo, tmp_path):
    agents = Judged()
    out = E2E_RETRIED.replace("✘  4", "✓  4")  # the evidence test passes on its retry too
    (tmp_path / "e2e.out").write_text(out)
    cmd = f"cat {tmp_path / 'e2e.out'}"
    team, _ = e2e_team(repo, tmp_path, agents=agents, command=cmd)
    assert agents.judged == 1  # the round went on to the tester
    packet = (team.home / "packets" / "T-002.md").read_text()
    section = packet.split("## Flaky e2e tests (failed, then passed on retry: not blocking)")[1]
    assert "- [phone] › e2e/viewer.spec.ts:19:3 › viewer: notre-dame" in section
    assert "- [desktop] › e2e/evidence.spec.ts:5:1 › panel stays ready" in section
    assert "FLAKY e2e" in (team.home / "events.log").read_text()
    (e2e,) = [c for c in agents.checks if c.get("kind") == "e2e"]  # what the judge sees
    assert e2e["ok"] and e2e["output"].startswith("FLAKY, not blocking")
    report = write_report(team).read_text()
    assert "T-002: [phone] › e2e/viewer.spec.ts:19:3 › viewer: notre-dame" in report


@pytest.mark.usefixtures("fast_poll")
def test_a_test_failing_twice_blocks_with_its_error_context(repo, tmp_path):
    agents = Judged()
    (tmp_path / "e2e.out").write_text(E2E_RETRIED)
    ctx = "web/test-results/pw/evidence-desktop"
    cmd = (
        f"mkdir -p {ctx} && echo '- button \"Retry\"' > {ctx}/error-context.md && "
        f"cat {tmp_path / 'e2e.out'} && exit 1"
    )
    team, _ = e2e_team(repo, tmp_path, agents=agents, loops=1, command=cmd)
    assert agents.judged == 0
    assert task(team, "T-002")["status"] == "blocked"
    assert "e2e FAILED: 1 failed, 1 passed." in agents.feedback
    assert '    - button "Retry"' in agents.feedback


@pytest.mark.usefixtures("fast_poll")
def test_a_timed_out_e2e_tells_the_developer_it_timed_out_not_failed(repo, tmp_path):
    agents = Judged()
    cmd = (
        "echo 'Running 3 tests using 1 worker' && "
        "echo '  ✓  1 [desktop] › e2e/a.spec.ts:1:1 › a (1.0s)' && sleep 30"
    )
    team, _ = e2e_team(repo, tmp_path, agents=agents, loops=1, command=cmd, mins=0.02)
    assert agents.judged == 0
    assert "did not finish within the time limit" in agents.feedback
    assert "they fail" not in agents.feedback
    assert (
        "TIMEOUT: e2e stopped after 0.02 min (not a test failure); 1 passed, 0 failed, "
        "2 not run." in agents.feedback
    )
    assert "TIMEOUT echo 'Running 3 tests" in (team.home / "events.log").read_text()


def test_git_fetch_retries_a_network_error_then_succeeds(repo, monkeypatch):
    team = team_for(repo)
    real, calls = GR.subprocess.run, []

    def flaky(cmd, **kw):
        if cmd[:2] == ["git", "fetch"]:
            calls.append(cmd)
            if len(calls) < 3:
                return SimpleNamespace(
                    returncode=128,
                    stdout=b"",
                    stderr=b"fatal: unable to access 'https://github.com/x/y.git/': "
                    b"Could not resolve host: github.com",
                )
        return real(cmd, **kw)

    monkeypatch.setattr(GR.subprocess, "run", flaky)
    team.fetch("dev")
    assert len(calls) == 3
    assert "git fetch: network error, retrying in 0s" in (team.home / "events.log").read_text()


def test_git_gives_up_after_the_retries_and_never_retries_other_errors(repo, monkeypatch):
    team = team_for(repo)
    calls = []

    def fails(cmd, **kw):
        calls.append(cmd)
        err = b"Could not resolve host: github.com" if cmd[1] == "fetch" else b"bad revision"
        return SimpleNamespace(returncode=128, stdout=b"", stderr=err)

    monkeypatch.setattr(GR.subprocess, "run", fails)
    with pytest.raises(RuntimeError, match="Could not resolve host"):
        team.git("fetch", "-q", "origin")
    assert len(calls) == 1 + len(GR.GIT_NET_WAITS)
    calls.clear()
    with pytest.raises(RuntimeError, match="bad revision"):
        team.git("rev-parse", "nope")  # not a network command: once
    assert len(calls) == 1
    calls.clear()
    monkeypatch.setattr(
        GR.subprocess,
        "run",
        lambda cmd, **kw: (
            calls.append(cmd)
            or SimpleNamespace(returncode=128, stdout=b"", stderr=b"couldn't find remote ref nope")
        ),
    )
    with pytest.raises(RuntimeError, match="remote ref"):
        team.git("fetch", "-q", "origin", "nope")  # a network command, not a network error
    assert len(calls) == 1


def test_web_developer_is_told_the_tester_runs_e2e():
    team = SimpleNamespace(cfg=CFG)
    assert "run by Kerege, not inside your sandbox" in e2e_rule(team, CFG["developers"]["dev_app"])
    assert e2e_rule(team, CFG["developers"]["dev_pipeline"]) == ""


# --- 3. "the same reason" = the same criterion number -----------------------------------------
def test_criterion_key_is_the_number_not_the_wording():
    crit = ["first", "/ lists the new runs"]
    a = {"number": 2, "criterion": "The root page lists the runs", "met": False}
    b = {"number": 2, "criterion": "Run list on / shows the dated runs", "met": False}
    assert criterion_key(a, crit) == criterion_key(b, crit) == "criterion #2"
    assert criterion_key({"number": 9, "criterion": "x"}, crit) == "criterion: x"  # out of range


def test_the_judge_numbers_its_criteria():
    assert "number" in R.TESTER_SYSTEM and "`number`" in R.TESTER_SYSTEM


# --- 1. test files are compared between rounds --------------------------------------------------
TWO = "def test_area():\n    assert area(2) == 4\n    assert area(3) == 9\n"
ONE = "def test_area():\n    assert area(2) == 4\n"
THREE = TWO + "    assert area(4) == 16\n"


def weakening_dev(later: str):
    """T-001 writes a test with two assertions in round 1, then `later` in every next round.
    The fake judge fails round 1 and passes from round 2 on."""

    class Dev(Fast):
        def develop(self, team, s):
            if s["task"]["id"] == "T-001":
                test = Path(s["worktree"]) / "pipeline" / "tests" / "test_area.py"
                test.parent.mkdir(exist_ok=True)
                test.write_text(TWO if not s.get("rounds") else later)
            return super().develop(team, s)

    return Dev()


@pytest.mark.usefixtures("fast_poll")
def test_an_assertion_removed_after_a_send_back_fails_and_blocks_on_resubmit(repo):
    """Base -> HEAD shows the new test with one assertion: nothing removed. Only the diff
    between rounds shows it, and resubmitting it unchanged is the same reason again."""
    team, graph = make_team(repo, weakening_dev(ONE))
    graph.invoke({}, {"configurable": {"thread_id": "r"}})
    t = {t["id"]: t for t in team.load_backlog()}["T-001"]
    assert t["status"] == "blocked" and "twice in a row" in t["blocked_reason"]
    found = [v for v in team.violations(time.strftime("%Y-%m-%d")) if v["task"] == "T-001"]
    assert found and "since the last round: removes test assertions" in found[0]["detail"]


@pytest.mark.usefixtures("fast_poll")
def test_a_test_made_stronger_after_a_send_back_passes(repo):
    team, graph = make_team(repo, weakening_dev(THREE))
    graph.invoke({}, {"configurable": {"thread_id": "r"}})
    assert {t["id"]: t for t in team.load_backlog()}["T-001"]["status"] == "done"


def judge_with(monkeypatch, verdict: dict, **state) -> tuple[dict, dict]:
    seen = {}

    def fake_llm_call(team, role, task, system, prompt, schema, **kw):
        seen.update(system=system, prompt=prompt, schema=schema, images=kw.get("images"))
        return copy.deepcopy(verdict)

    monkeypatch.setattr(R, "llm_call", fake_llm_call)
    team = SimpleNamespace(
        remote_name="origin", cfg=CFG, git=lambda *a, **k: "", redact=str, root=HERE.parent
    )
    s = {"task": {"id": "T-1", "title": "t"}, "plan": "p", "criteria": ["c"], "worktree": "."}
    return R.RealAgents().judge(team, {**s, **state}, []), seen


PASS = {
    "passed": True,
    "criteria": [{"number": 1, "criterion": "c", "met": True, "note": ""}],
    "tests_weakened": True,
    "feedback": "",
    "summary": "",
    "how_to_try": "",
}


def test_the_judge_sees_the_test_diff_and_tests_weakened_fails_the_task(monkeypatch):
    diff = "-    expect(date).toBe('16 қаз.')\n+    expect(date).toBe(intlFormat(d))\n"
    v, seen = judge_with(monkeypatch, PASS, test_changes=diff)
    assert "<test_changes_since_last_round>\n" + diff in seen["prompt"]
    assert "tests_weakened" in seen["schema"]["required"]
    assert v["passed"] is False and "weaken the tests" in v["feedback"]
    # no test diff (round 1): the flag alone cannot fail it
    v, seen = judge_with(monkeypatch, PASS)
    assert v["passed"] is True and "test_changes_since_last_round" not in seen["prompt"]


def test_tester_sendbacks_name_the_exact_place_and_judge_one_part():
    """Owner, 2026-10-23: U2 was sent back three times on small screenshot points."""
    assert "A\nsend-back must be fixable in ONE round" in R.TESTER_SYSTEM
    assert "file and\nline in the diff" in R.TESTER_SYSTEM and "CSS selector" in R.TESTER_SYSTEM
    assert "which screenshot, which element" in R.TESTER_SYSTEM
    assert 'never a\nvague "adjust the layout"' in R.TESTER_SYSTEM
    assert 'A <plan> that says "part k of n"' in R.TESTER_SYSTEM
    assert "Judge only this part's\n<acceptance_criteria>" in R.TESTER_SYSTEM
    assert "built in 2-3 `parts` inside the same task (never new backlog tasks)" in R.BRAIN_SYSTEM


def test_fixtures_rule_is_in_the_dev_tester_and_brain_prompts():
    rule = "Expected values come from fixtures, never from the code under test"
    assert rule in R.TESTER_SYSTEM
    assert rule in Path(R.__file__).read_text().split("def _brief", 1)[1].split("def _claude")[0]
    assert "never values computed by the code\n  under test" in R.BRAIN_SYSTEM


def test_images_over_the_request_limit_are_named_never_silently_cut(monkeypatch, tmp_path):
    shots = []
    for name in ("root-ru.png", "root-kk.png", "dev-root.png"):
        (tmp_path / name).write_bytes(b"x" * 100)
        shots.append(str(tmp_path / name))
    monkeypatch.setattr(R, "MAX_IMAGES", 2)
    _v, seen = judge_with(monkeypatch, {**PASS, "tests_weakened": False}, screenshots=shots)
    assert seen["images"] == shots[:2]
    assert "dev-root.png: not attached (API request size limit)" in seen["prompt"]


def test_quick_benchmark_brief_only_for_pipeline_and_with_dev_baseline(repo):
    team = team_for(repo, gates=bench_repo(repo, "90"))
    team.fetch("dev")
    s = {"task": {"id": "P3"}}
    devs = team.cfg["developers"]
    assert quick_benchmark(team, s, devs["dev_app"]) == ""
    text = quick_benchmark(team, s, devs["dev_pipeline"])
    assert "--only notre-dame,room" in text and "No cached dev baseline" in text
    G.benchmark(team, team.git("rev-parse", "devteam-fake/dev").strip())
    assert "| room | 90.0 |" in quick_benchmark(team, s, devs["dev_pipeline"])
    s["task"]["quick_only"] = ["notre-dame", "aegviidu"]  # a task picks the videos it needs
    assert "--only notre-dame,aegviidu" in quick_benchmark(team, s, devs["dev_pipeline"])


def test_benchmark_cache_survives_merges_that_leave_pipeline_alone(repo):
    team = team_for(repo, gates=bench_repo(repo, "90"))
    first = git(repo, "rev-parse", "HEAD").strip()
    assert G.benchmark(team, first)["videos"]["room"]["registered_pct"] == 90
    (repo / "web" / "app.ts").write_text("export const x = 1\n")
    git(repo, "commit", "-qam", "web only")
    team.cfg["gates"]["benchmark"]["command"] = "false"  # a re-run would fail
    assert G.benchmark(team, git(repo, "rev-parse", "HEAD").strip()) is not None
    (repo / "pipeline" / "score.txt").write_text("91")
    git(repo, "commit", "-qam", "pipeline change")
    assert G.benchmark(team, git(repo, "rev-parse", "HEAD").strip()) is None  # re-ran, failed


def test_per_video_tolerance():
    v = {"registered_pct": 85, "models": 3, "reproj_px": 0.64, "points": 13384}
    noisy = {**v, "reproj_px": 0.74, "points": 10845}  # two identical runs of mykolaiv
    cfg = CFG["gates"]["benchmark"]
    assert G.regressions({"videos": {"mykolaiv": v}}, {"videos": {"mykolaiv": noisy}}, cfg) == []
    assert len(G.regressions({"videos": {"other": v}}, {"videos": {"other": noisy}}, cfg)) == 2


def test_report_shows_subscription_load_per_task(repo):
    team = team_for(repo)
    costs.record(
        team.home,
        "dev_pipeline",
        "claude-code (subscription)",
        "P2a",
        9e5,
        2e4,
        4.2,
        False,
        secs=1800,
    )
    costs.record(
        team.home,
        "dev_pipeline",
        "claude-code (subscription)",
        "P2a",
        1e5,
        1e4,
        0.8,
        False,
        secs=600,
    )
    costs.record(
        team.home, "dev_app", "codex (subscription)", "A1a", 5e5, 3e4, 0.0, False, secs=1200
    )
    text = write_report(team).read_text()
    assert "## Subscription load per task" in text
    assert (
        "| P2a | dev_pipeline | claude-code (subscription) | 2 | 40 | 1,000,000 | 30,000 | 5.00 |"
        in text
    )
    assert "| A1a | dev_app | codex (subscription) | 1 | 20 | 500,000 | 30,000 | — |" in text
    json.dumps(text)


def test_mykolaiv_stays_ungated_by_the_owners_decision():
    cfg = yaml.safe_load((HERE / "config.yaml").read_text())["gates"]["benchmark"]
    assert not G.gated(cfg, "mykolaiv")
    why = cfg["per_video"]["mykolaiv"]["why"]
    assert "owner 2026-10-21" in why and "P3c" in why
    assert "gate again after P3" not in (HERE / "config.yaml").read_text()


def test_the_tester_compares_screenshots_with_the_v8_reference_screens(monkeypatch, tmp_path):
    """Owner 2026-10-24: design system v8 is the UI's source of truth; the tester gets the
    reference screens the task names, after its own shots and before the dev-* ones."""
    shot, before = tmp_path / "home-1440x900-light.png", tmp_path / "dev-home-1440x900-light.png"
    shot.write_bytes(b"png")
    before.write_bytes(b"png")
    goal = "Match docs/design/screens/sign-in-light.png; docs/design/screens/missing-light.png."
    task = {"id": "DS1", "title": "t", "goal": goal}
    v, seen = judge_with(monkeypatch, PASS, task=task, screenshots=[str(before), str(shot)])
    ref = str(HERE.parent / "docs" / "design" / "screens" / "sign-in-light.png")
    assert seen["images"] == [str(shot), ref, str(before)]
    assert "each one comes right after its own label" in seen["prompt"]
    assert [R.image_label(p).split(": ")[1].split(" (")[0] for p in seen["images"]] == [
        "AFTER",
        "REFERENCE v8",
        "BEFORE",
    ]
    assert "<owner_approved_test_rewrites>\nweb/src/design/tokens.test.ts" in seen["prompt"]
    assert v["passed"] is True  # round 1: no test diff, the approved rewrite is not weakening
    assert "every colour, font or component that differs" in R.TESTER_SYSTEM
    assert "only in spacing, alignment, size" in R.TESTER_SYSTEM
    # a task with no reference and no approved rewrite: neither block
    v, seen = judge_with(monkeypatch, PASS, screenshots=[str(shot)])
    assert seen["images"] == [str(shot)] and "owner_approved_test_rewrites" not in seen["prompt"]
    assert "v8 is the source of truth" in R.design_rule(CFG["developers"]["dev_app"])
    assert "390x844" in R.design_rule(CFG["developers"]["dev_app"])


# --- gate integrity (Kerege v2 stage A, 2026-10-27) -----------------------------------------
def verdict(*nums, met=True) -> dict:
    crit = [{"number": n, "criterion": f"c{n}", "met": met, "note": ""} for n in nums]
    return {**PASS, "tests_weakened": False, "criteria": crit}


def judge_seq(monkeypatch, verdicts: list[dict], n=3, team=None, **state) -> tuple[dict, list]:
    calls = []

    def fake_llm_call(team, role, task, system, prompt, schema, **kw):
        calls.append(prompt)
        return copy.deepcopy(verdicts[len(calls) - 1])

    monkeypatch.setattr(R, "llm_call", fake_llm_call)
    team = team or SimpleNamespace(
        remote_name="origin", cfg=CFG, git=lambda *a, **k: "", redact=str, root=HERE.parent
    )
    s = {"task": {"id": "T-1", "title": "t"}, "plan": "p", "worktree": "."}
    s["criteria"] = [f"c{i}" for i in range(1, n + 1)]
    return R.RealAgents().judge(team, {**s, **state}, []), calls


@pytest.mark.parametrize(
    ("first", "gaps"),
    [
        (verdict(1, 2), "misses numbers [3]"),
        (verdict(), "misses numbers [1, 2, 3]"),
        (verdict(1, 2, 2, 3), "repeats numbers [2]"),
        (verdict(1, 2, 3, 4), "has numbers outside 1..3 [4]"),
        (verdict(0, 1, 2, 3), "has numbers outside 1..3 [0]"),
        (verdict(1, 2, "3"), "misses numbers [3]; has numbers outside 1..3 ['3']"),
        (verdict(1, 2, None), "misses numbers [3]; has numbers outside 1..3 [None]"),
    ],
)
def test_an_incomplete_verdict_never_passes(monkeypatch, first, gaps):
    """2026-10-27: passed = passed and all(met) let a verdict that left a criterion out (or
    repeated one) pass. It is asked once more with what was wrong, then it is a tester error."""
    assert R.verdict_gaps(first, 3) == gaps
    v, calls = judge_seq(monkeypatch, [first, verdict(1, 2, 3)])  # the re-ask is complete
    assert v["passed"] is True and "tester_error" not in v
    assert len(calls) == 2 and gaps in calls[1] and gaps not in calls[0]
    v, calls = judge_seq(monkeypatch, [first, first])  # still incomplete
    assert v["passed"] is False and len(calls) == 2
    assert v["tester_error"] == f"the tester's verdict {gaps} (asked twice)"
    assert "not counted against you" in v["feedback"]


def test_a_complete_verdict_is_asked_once_and_passes_only_when_all_are_met(monkeypatch):
    v, calls = judge_seq(monkeypatch, [verdict(3, 1, 2)])
    assert v["passed"] is True and len(calls) == 1 and "tester_error" not in v
    v, calls = judge_seq(monkeypatch, [verdict(1, 2, 3, met=False)])
    assert v["passed"] is False and len(calls) == 1 and "tester_error" not in v
    v, _ = judge_seq(monkeypatch, [{**verdict(1, 2, 3), "passed": False}])
    assert v["passed"] is False


def test_lockfiles_are_not_judged_and_an_oversized_diff_cannot_pass(repo, monkeypatch):
    team = team_for(repo)
    team.fetch("dev")
    git(repo, "checkout", "-qb", "feat")
    (repo / "web" / "app.ts").write_text("export const a = 1;\n")
    lock = "lock-line-that-is-generated\n" * 6000  # ~170 k characters
    for f in ("web/pnpm-lock.yaml", "pipeline/uv.lock", "web/package-lock.json"):
        (repo / f).write_text(lock)
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "feat")
    v, calls = judge_seq(monkeypatch, [verdict(1)], n=1, team=team, worktree=str(repo))
    assert v["passed"] is True and len(calls) == 1
    assert "+export const a = 1;" in calls[0] and "lock-line" not in calls[0]
    assert "web/pnpm-lock.yaml" in calls[0] and "pipeline/uv.lock" in calls[0]  # --stat keeps them
    # the remaining diff over the limit: no tester call, the round cannot pass
    (repo / "web" / "big.ts").write_text("export const x = 'y';\n" * 6000)
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "big")
    v, calls = judge_seq(monkeypatch, [verdict(1)], n=1, team=team, worktree=str(repo))
    assert calls == [] and v["passed"] is False
    assert "diff is too large to review" in v["feedback"] and "within the plan" in v["feedback"]
    assert f"limit of {R.MAX_DIFF:,}" in v["feedback"]
    # the owner's per-task `tester: {max_diff: N}` (UI-report, 2026-10-29) lets it be judged;
    # a smaller one still refuses, and other tasks keep MAX_DIFF
    task = {"id": "T-1", "title": "t", "tester": {"max_diff": 180_000}}
    v, calls = judge_seq(monkeypatch, [verdict(1)], n=1, team=team, worktree=str(repo), task=task)
    assert v["passed"] is True and len(calls) == 1
    task["tester"]["max_diff"] = 100_000
    v, calls = judge_seq(monkeypatch, [verdict(1)], n=1, team=team, worktree=str(repo), task=task)
    assert calls == [] and "limit of 100,000" in v["feedback"]


class TesterErrs(Fast):
    """The tester's verdict is incomplete in the first `errors` rounds, then the fake's."""

    errors = 1

    def judge(self, team, s, checks):
        if s.get("rounds", 0) <= self.errors:
            err = "the tester's verdict misses numbers [1]"
            return {"passed": False, "criteria": [], "feedback": "", "summary": "",
                    "how_to_try": "", "tester_error": err}  # fmt: skip
        return {**super().judge(team, s, checks), "passed": True}


def test_a_tester_error_is_logged_and_not_counted_against_the_developer(repo):
    agents = TesterErrs()
    agents.plan = only("T-001", "dev_pipeline")
    team, graph = make_team(repo, agents, max_fix_loops=0)  # no send-back allowed at all
    graph.invoke({}, {"configurable": {"thread_id": "r"}})
    assert task_status(team, "T-001") == "done"  # the tester error was not a send-back
    (row,) = [json.loads(x) for x in (team.home / "tester_errors.jsonl").read_text().splitlines()]
    assert row["task"] == "T-001" and "misses numbers [1]" in row["detail"]
    assert "TESTER ERROR (not counted against the developer)" in events(team)
    assert "sent back: 0" in (team.home / "packets" / "T-001.md").read_text()


def test_tester_errors_twice_in_a_row_end_the_task(repo):
    agents = TesterErrs()
    agents.errors, agents.plan = 9, only("T-001", "dev_pipeline")
    team, graph = make_team(repo, agents, max_fix_loops=3)
    graph.invoke({}, {"configurable": {"thread_id": "r"}})
    t = {t["id"]: t for t in team.load_backlog()}["T-001"]
    assert t["status"] == "blocked" and "twice in a row" in t["blocked_reason"]
    assert len((team.home / "tester_errors.jsonl").read_text().splitlines()) == 2


# --- tester images and API rejections (cont-20261027-232407, 2026-10-28) --------------------
def png(path: Path, w: int, h: int) -> str:
    Image.new("RGB", (w, h), "white").save(path)
    return str(path)


def sent_size(data: bytes) -> tuple[int, int]:
    from io import BytesIO

    with Image.open(BytesIO(data)) as im:
        assert im.format == "PNG"
        return im.size


def test_a_tall_image_is_scaled_to_2000_px_aspect_kept(tmp_path):
    """24 screenshots + v8 references: the API refused a reference over 2000 px (HTTP 400)."""
    tall, small = png(tmp_path / "ref.png", 1440, 3600), png(tmp_path / "root.png", 1440, 900)
    assert sent_size(R.png_data(tall)) == (800, 2000)
    assert sent_size(R.png_data(tall, 0.5)) == (400, 1000)
    assert R.png_data(small) == Path(small).read_bytes()  # under the limit: sent as it is
    assert sent_size(R.png_data(small, 0.5)) == (720, 450)


def test_llm_call_sends_every_image_scaled_with_its_label(repo, tmp_path):
    from test_safety import StubClient

    team = team_for(repo)
    shots = [png(tmp_path / "root.png", 1440, 3600), png(tmp_path / "dev-root.png", 1440, 900)]
    seen = []
    client = StubClient()
    create = client.create
    client.create = lambda **kw: seen.append(kw) or create(**kw)
    R.llm_call(team, "tester", "T", "s", "p", {}, client=client, images=shots)
    blocks = seen[0]["messages"][0]["content"]
    images = [b for b in blocks if b["type"] == "image"]
    labels = [b["text"] for b in blocks if b["type"] == "text"]
    assert len(images) == 2 and labels[:2] == [R.image_label(i) for i in shots]
    sizes = [sent_size(R.base64.b64decode(i["source"]["data"])) for i in images]
    assert sizes == [(800, 2000), (1440, 900)]


def rejected() -> anthropic.BadRequestError:
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    body = {"type": "error", "error": {"type": "invalid_request_error", "message": "too big"}}
    return anthropic.BadRequestError(
        "too big", response=httpx.Response(400, request=req), body=body
    )


def judge_rejected(monkeypatch, answers: list) -> tuple[dict | Exception, list, list]:
    """The tester's API call answers in turn: an exception is raised, a verdict returned."""
    scales, errors = [], []

    def fake_llm_call(team, role, task, system, prompt, schema, **kw):
        scales.append(kw["image_scale"])
        a = answers[len(scales) - 1]
        if isinstance(a, Exception):
            raise a
        return copy.deepcopy(a)

    monkeypatch.setattr(R, "llm_call", fake_llm_call)
    team = SimpleNamespace(
        remote_name="origin", cfg=CFG, git=lambda *a, **k: "", redact=str, root=HERE.parent,
        tester_error=lambda tid, detail: errors.append((tid, detail)),
    )  # fmt: skip
    s = {"task": {"id": "T-1", "title": "t"}, "plan": "p", "worktree": ".", "criteria": ["c1"]}
    try:
        return R.RealAgents().judge(team, s, []), scales, errors
    except Exception as e:
        return e, scales, errors


def test_an_api_rejection_is_a_tester_error_retried_once_at_half_size(monkeypatch):
    v, scales, errors = judge_rejected(monkeypatch, [rejected(), verdict(1)])
    assert v["passed"] is True and scales == [1.0, 0.5]
    ((tid, detail),) = errors
    assert tid == "T-1" and detail == "API error: too big; retrying with images at half size"
    e, scales, errors = judge_rejected(monkeypatch, [rejected(), rejected()])
    assert isinstance(e, R.TaskHalted) and str(e).startswith("tester error: ")
    assert scales == [1.0, 0.5] and len(errors) == 2
    e, scales, errors = judge_rejected(monkeypatch, [anthropic.APIConnectionError(
        request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"))])  # fmt: skip
    assert isinstance(e, anthropic.APIConnectionError) and errors == []  # not the request's fault


class TesterRejected(Fast):
    def judge(self, team, s, checks):
        raise R.TaskHalted("tester error: Error code: 400 - invalid_request_error")


def test_a_tester_rejected_twice_blocks_the_task_not_as_a_developer_return(repo):
    agents = TesterRejected()
    agents.plan = only("T-001", "dev_pipeline")
    team, graph = make_team(repo, agents, max_fix_loops=3)
    graph.invoke({}, {"configurable": {"thread_id": "r"}})
    t = {t["id"]: t for t in team.load_backlog()}["T-001"]
    assert t["status"] == "blocked" and t["blocked_reason"].startswith("tester error: ")
    assert "sent back" not in events(team)


def task_status(team, tid):
    return {t["id"]: t for t in team.load_backlog()}[tid]["status"]


def events(team):
    return (team.home / "events.log").read_text()


# --- the judged diff of a part (PR #87 review: whole-branch diffs of RPT1, U2, REV1-api, DS1
# were 154-359 k characters, so step 0 would have failed each last part as too large) ----------
def commit(repo, name: str, chars: int, branch="feat") -> str:
    git(repo, "checkout", "-q", branch)
    line = f"export const {name.replace('-', '_')} = 'x';\n"
    (repo / "web" / f"{name}.ts").write_text(line * (chars // len(line)))
    git(repo, "add", ".")
    git(repo, "commit", "-qm", name)
    return git(repo, "rev-parse", "HEAD").strip()


def judged(monkeypatch, team, repo, part_base="") -> tuple[dict, list]:
    return judge_seq(monkeypatch, [verdict(1)], n=1, team=team, worktree=str(repo),
                     part_base=part_base)  # fmt: skip


@pytest.mark.parametrize("chars", [60_000, 100_000])
def test_each_part_is_judged_on_its_own_diff(repo, monkeypatch, chars):
    """3 parts of ~60 k / ~100 k: the whole branch is ~180 k / ~300 k, over MAX_DIFF, yet every
    part fits; --stat still shows the whole branch."""
    team = team_for(repo)
    team.fetch("dev")
    git(repo, "checkout", "-qb", "feat")
    base = ""
    for k in (1, 2, 3):
        head = commit(repo, f"part{k}", chars)
        v, calls = judged(monkeypatch, team, repo, base)
        assert v["passed"] is True and len(calls) == 1, k
        assert f"export const part{k}" in calls[0]
        assert all(f"export const part{i}" not in calls[0] for i in range(1, k))  # accepted ones
        assert all(f"web/part{i}.ts" in calls[0] for i in range(1, k + 1))  # --stat: all parts
        base = head  # the tester accepted part k here
    whole = team.git("diff", f"{team.remote_name}/dev...HEAD", cwd=repo)
    assert len(whole) > R.MAX_DIFF  # judged whole, the last part would have failed


def test_a_part_over_the_limit_still_fails(repo, monkeypatch):
    team = team_for(repo)
    team.fetch("dev")
    git(repo, "checkout", "-qb", "feat")
    accepted = commit(repo, "part1", 10_000)
    commit(repo, "part2", 130_000)
    v, calls = judged(monkeypatch, team, repo, accepted)
    assert calls == [] and v["passed"] is False and "too large to review" in v["feedback"]
    v, calls = judged(monkeypatch, team, repo)  # a task in one piece: the whole branch, as before
    assert calls == [] and v["passed"] is False


def test_a_dev_merge_between_parts_does_not_enlarge_the_part_diff(repo, monkeypatch):
    team = team_for(repo)
    team.fetch("dev")
    git(repo, "checkout", "-qb", "feat")
    accepted = commit(repo, "part1", 60_000)
    commit(repo, "from-dev", 150_000, branch="dev")  # someone else's 150 k lands on dev
    git(repo, "push", "-q", remote_of(repo), "dev")
    team.fetch("dev")
    git(repo, "checkout", "-q", "feat")
    git(repo, "merge", "-q", "--no-edit", f"{team.remote_name}/dev")  # Kerege merges dev in
    commit(repo, "part2", 20_000)
    v, calls = judged(monkeypatch, team, repo, accepted)
    assert v["passed"] is True and len(calls) == 1
    assert "export const part2" in calls[0]
    assert "export const from_dev" not in calls[0] and "export const part1" not in calls[0]
    # dev merged in before the part's first commit: the part diff is still only the part
    commit(repo, "more-dev", 150_000, branch="dev")
    git(repo, "push", "-q", remote_of(repo), "dev")
    team.fetch("dev")
    git(repo, "checkout", "-q", "feat")
    git(repo, "merge", "-q", "--no-edit", f"{team.remote_name}/dev")
    v, calls = judged(monkeypatch, team, repo, accepted)
    assert v["passed"] is True and "export const more_dev" not in calls[0]
