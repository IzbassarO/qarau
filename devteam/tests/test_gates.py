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

import pytest
import yaml
from conftest import CFG, HERE, git, remote_of, team_for
from langgraph.checkpoint.sqlite import SqliteSaver

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

    def judge(self, team, s, checks):
        self.judged += 1
        return super().judge(team, s, checks)

    def develop(self, team, s):
        self.feedback = s.get("feedback") or self.feedback
        return super().develop(team, s)


def e2e_team(repo, tmp_path, code=0, dev="dev_app", agents=None, loops=0):
    agents = agents or Fast()
    agents.plan = only("T-002" if dev == "dev_app" else "T-001", dev)
    devs = copy.deepcopy(CFG["developers"])
    for d in devs.values():
        d.update(setup=[], checks=[])
        d.pop("screenshots", None)
    gates = copy.deepcopy(CFG["gates"])
    log = tmp_path / "e2e.log"
    gates["e2e"]["command"] = E2E.format(py=sys.executable, log=str(log), port="{port}", code=code)
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
    team = SimpleNamespace(remote_name="origin", cfg=CFG, git=lambda *a, **k: "", redact=str)
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
