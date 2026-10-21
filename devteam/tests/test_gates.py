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
from devteam import gates as G
from devteam.agents import FakeAgents
from devteam.gates import FakeGitHub, GitHub, regressions, release, tag_release
from devteam.graph import Team, build_graph
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
