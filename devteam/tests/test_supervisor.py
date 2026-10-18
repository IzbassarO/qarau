"""Owner, 2026-10-17 (PR 1): supervisor limits, loop detection, dev merged before each round,
plans inside the developer's folders, stop / resume one task, dashboard from the current run,
prompt caching, `writable` data folders, the A7 split and the backlog order."""

import copy
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from conftest import CFG, HERE, git, remote_of
from langgraph.checkpoint.sqlite import SqliteSaver

from devteam import cli, costs
from devteam import continuous as C
from devteam import dashboard as D
from devteam import graph as GR
from devteam import supervise as SV
from devteam.agents import FakeAgents
from devteam.graph import PlanError, Team, build_graph, outside_paths, validate_plan
from devteam.real import RealAgents, llm_call
from devteam.supervise import TaskHalted

STREAM = (HERE / "tests" / "claude-stream.jsonl").read_text()  # real claude 2.1.302 output


class Fast(FakeAgents):
    delay = 0


@pytest.fixture(autouse=True)
def fast_poll(monkeypatch):
    monkeypatch.setattr(GR, "POLL_S", 0.1)


def make(repo, agents=None, secrets=None, **cfg_over):
    cfg = copy.deepcopy(CFG)
    cfg.update(cfg_over)
    team = Team(repo, cfg, secrets or {}, agents or Fast(), fake=True, remote=remote_of(repo))
    graph = build_graph(team, SqliteSaver(sqlite3.connect(":memory:", check_same_thread=False)))
    return team, graph


def statuses(team):
    return {t["id"]: t["status"] for t in team.load_backlog()}


def task(team, tid):
    return next(t for t in team.load_backlog() if t["id"] == tid)


def plan(tid, dev, criteria=("stub file exists",)):
    return {"task_id": tid, "assignee": dev, "plan": "p", "criteria": list(criteria)}


# --- 1. supervisor -----------------------------------------------------------------------------
def test_token_counters_read_what_the_tools_print(tmp_path):
    claude = SV.ClaudeTokens()
    half = len(STREAM) // 2  # a chunk may end mid-line
    claude.feed(STREAM[:half].encode())
    assert claude.feed(STREAM[half:].encode()) == 2 + 18787 + 11227 + 4
    # one API message split into several events counts once (latest usage wins)
    msg = {"type": "assistant", "message": {"id": "m1", "usage": {"input_tokens": 10}}}
    again = {"type": "assistant", "message": {"id": "m1", "usage": {"input_tokens": 12}}}
    assert SV.ClaudeTokens().feed(f"{json.dumps(msg)}\n{json.dumps(again)}\n".encode()) == 12

    day = tmp_path / "sessions" / "2026" / "10" / "17"
    day.mkdir(parents=True)
    rollout = day / "rollout-2026-10-17T10-00-00-abc-123.jsonl"

    def count(total_in, total_out):
        u = {"input_tokens": total_in, "cached_input_tokens": 0, "output_tokens": total_out}
        e = {
            "type": "event_msg",
            "payload": {"type": "token_count", "info": {"total_token_usage": u}},
        }
        with open(rollout, "a") as f:
            f.write(json.dumps(e) + "\n")

    count(1000, 10)
    codex = SV.CodexTokens(home=tmp_path)
    assert codex.feed(b"") == 0  # no thread yet
    assert codex.feed(b'{"type":"thread.started","thread_id":"abc-123"}\n') == 1010
    count(5000, 50)
    assert codex.feed(b"") == 5050
    resumed = SV.CodexTokens("abc-123", home=tmp_path)  # a resumed session counts this round
    count(9000, 60)
    assert resumed.feed(b'{"type":"thread.started","thread_id":"abc-123"}\n') == 9060 - 5050


def test_round_watch_limits():
    lim = {"max_tokens_per_round": 100, "max_tokens_per_task": 150, "max_round_min": 1}
    counter = SimpleNamespace(n=0, feed=lambda chunk: counter.n)
    w = SV.RoundWatch(counter, 0, lim)
    assert w(b"", 10) == ""
    counter.n = 100
    assert "this round used" in w(b"", 10) and "limit 0.0001 M per round" in w(b"", 10)
    counter.n = 60
    assert "the task used" in SV.RoundWatch(counter, 90, lim)(b"", 10)
    assert "without a new commit (limit 1 min)" in SV.RoundWatch(counter, 0, lim)(b"", 61)


FAKE_CLAUDE = """#!{py}
import json, subprocess, sys, time
sys.stdin.read()
open({pid!r}, "w").write(str(subprocess.Popen(["sleep", "60"]).pid))  # a child it leaves
msg = {{"type": "assistant", "message": {{"id": "m", "usage": {{"input_tokens": 5000}}}}}}
print(json.dumps(msg), flush=True)
time.sleep(60)
"""


def alive(pid: int) -> bool:
    r = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True)
    return bool(r.stdout.strip()) and "Z" not in r.stdout


def test_round_over_the_token_limit_is_killed_with_its_process_group(repo, tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    pid = tmp_path / "child.pid"
    (bindir / "claude").write_text(FAKE_CLAUDE.format(py=sys.executable, pid=str(pid)))
    (bindir / "claude").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
    team, _ = make(
        repo,
        secrets={"CLAUDE_CODE_OAUTH_TOKEN": "oauth-0123456789"},
        supervisor={"max_tokens_per_round": 1000},
    )
    s = {
        "task": {"id": "T-001", "title": "t", "goal": "g"},
        "assignee": "dev_pipeline",
        "plan": "p",
        "criteria": ["c"],
        "worktree": str(repo),
    }
    t0 = time.monotonic()
    with pytest.raises(TaskHalted, match=r"this round used 0.0 M .*limit 0.001 M per round"):
        RealAgents().develop(team, s)
    assert time.monotonic() - t0 < 20  # not the 60 s the tool would have run
    assert not alive(int(pid.read_text()))  # its child went with it
    (row,) = costs.subscription_rows(team.home, costs._today())
    assert row[0] == "T-001" and row[5] == 5000  # the tokens it used still count


class HaltT001(Fast):
    """dev_pipeline's T-001 round goes over a limit after writing a file; the rest is normal."""

    def develop(self, team, s):
        out = super().develop(team, s)
        if s["task"]["id"] == "T-001":
            raise TaskHalted("supervisor: this round used 9.0 M subscription tokens (limit 8 M)")
        return out


def test_a_task_over_a_limit_is_blocked_and_the_others_keep_going(repo):
    team, graph = make(repo, HaltT001())
    s = C.Continuous(team, graph).run()
    assert statuses(team) == {"T-001": "blocked", "T-002": "done", "T-003": "done"}
    reason = task(team, "T-001")["blocked_reason"]
    assert (
        reason.startswith("supervisor: this round used 9.0 M")
        and "`devteam resume T-001`" in reason
    )
    branch = team.branch_for("T-001")  # what the round wrote is kept on its branch
    assert "devteam-fake-T-001.txt" in git(repo, "show", "--stat", branch)
    assert git(remote_of(repo), "show", "dev:devteam/backlog.yaml").count("status: blocked") == 1
    assert s["stop_reason"] == "no plannable task left for any developer"
    d = D.collect(team, None)
    assert any(k == "blocked" and "T-001" in t and "supervisor" in t for k, t, _u in d["attention"])


class Tokens(Fast):
    def develop(self, team, s):
        return {**super().develop(team, s), "tokens": 10}


def test_task_token_limit_stops_before_the_next_round(repo):
    agents = Tokens()
    agents.plan = lambda team, todo, feedback=None, devs=None: [plan("T-001", "dev_pipeline")]
    team, graph = make(repo, agents, supervisor={"max_tokens_per_task": 10})
    graph.invoke({}, {"configurable": {"thread_id": "r"}})  # the fake tester fails round 1
    t = task(team, "T-001")
    assert t["status"] == "blocked" and "the task used 0.0 M" in t["blocked_reason"]


# --- 2. loop detection ---------------------------------------------------------------------
class SameReason(Fast):
    rounds = 0

    def develop(self, team, s):
        SameReason.rounds += 1
        return super().develop(team, s)

    def judge(self, team, s, checks):
        unmet = s["rounds"] % 2 if self.vary else 0  # vary: a different criterion each time
        return {
            "passed": False,
            "feedback": f"backend half missing ({s['rounds']})",
            "criteria": [{"criterion": c, "met": i != unmet} for i, c in enumerate(s["criteria"])],
            "summary": "s",
            "how_to_try": "t",
        }


@pytest.mark.parametrize("vary", [False, True])
def test_sent_back_twice_for_the_same_reason_blocks_without_another_round(repo, vary):
    agents = SameReason()
    agents.vary, SameReason.rounds = vary, 0
    agents.plan = lambda team, todo, feedback=None, devs=None: [
        plan("T-001", "dev_pipeline", ["web ok", "api ok"])
    ]
    team, graph = make(repo, agents)  # max_fix_loops: 3
    graph.invoke({}, {"configurable": {"thread_id": "r"}})
    t = task(team, "T-001")
    assert t["status"] == "blocked"
    if vary:  # different reasons: the usual fix-loop limit
        assert SameReason.rounds == 4 and "twice in a row" not in t["blocked_reason"]
    else:
        assert SameReason.rounds == 2
        assert "twice in a row for the same reason" in t["blocked_reason"]
        assert "backend half missing (2)" in t["blocked_reason"]


# --- 3. dev merged in before every round (conflicts: tests/test_gates.py) -------------------
def test_dev_is_merged_in_before_every_round(repo):
    seen = []

    class Watch(Fast):
        def develop(self, team, s):
            seen.append((Path(s["worktree"]) / "pipeline" / "late.py").exists())
            if not seen[1:]:  # while round 1 runs, dev moves on
                other = repo.parent / "other"
                git(repo.parent, "clone", "-q", "-b", "dev", remote_of(repo), str(other))
                (other / "pipeline" / "late.py").write_text("x = 1\n")
                git(other, "add", ".")
                git(other, "commit", "-qm", "late")
                git(other, "push", "-q", "origin", "dev")
            return super().develop(team, s)

    agents = Watch()
    agents.plan = lambda team, todo, feedback=None, devs=None: [plan("T-001", "dev_pipeline")]
    team, graph = make(repo, agents)
    graph.invoke({}, {"configurable": {"thread_id": "r"}})
    assert seen == [False, True]  # round 2 already had dev's new file, merged by the orchestrator
    assert statuses(team)["T-001"] == "done"


# --- 4. plans stay inside the developer's folders --------------------------------------------
def test_outside_paths():
    t = {"id": "A7"}
    assert outside_paths(["backend/tests/test_roi.py exists; web/src ok"], "dev_app", CFG, t) == [
        "backend/tests/test_roi.py"
    ]
    assert outside_paths(
        ["/viewer/notre-dame shows a box; docs/notes/x.md"], "dev_app", CFG, t
    ) == [
        "docs/notes/x.md"  # dev_app owns web/ only
    ]
    assert (
        outside_paths(
            ["runs on data/samples, writes out/web/manifest.json"], "dev_pipeline", CFG, t
        )
        == []
    )
    assert outside_paths(["edit devteam/config.yaml"], "dev_pipeline", CFG, t) == [
        "devteam/config.yaml"
    ]
    with pytest.raises(PlanError) as e:
        validate_plan(
            [plan("T-002", "dev_app", ["backend/app/roi.py has the routes"])],
            [{"id": "T-002", "area": "app"}],
            CFG,
        )
    assert e.value.outside == {"T-002": ["backend/app/roi.py"]} and e.value.only_outside
    assert "needs_split" in str(e.value)


def test_a_task_needing_other_folders_is_never_planned_but_blocked_for_a_split(repo):
    calls = []

    def brain(team, todo, feedback=None, devs=None):
        calls.append(feedback)
        out = []
        if "dev_app" in devs:
            out.append(plan("T-002", "dev_app", ["backend/app/roi.py stores the box"]))
        if "dev_pipeline" in devs:
            out.append(plan("T-001", "dev_pipeline"))
        return out

    agents = Fast()
    agents.plan = brain
    team, graph = make(repo, agents)
    graph.invoke({}, {"configurable": {"thread_id": "r"}})
    assert len(calls) == 2 and "backend/app/roi.py" in calls[1]  # told why, once
    t = task(team, "T-002")
    assert t["status"] == "blocked" and t["blocked_reason"].startswith("needs a split")
    assert statuses(team)["T-001"] == "done"  # the rest of the plan went ahead
    assert "T-002 blocked (needs a split)" in git(remote_of(repo), "log", "--format=%s", "dev")


def test_brain_can_ask_for_a_split_itself(repo):
    agents = Fast()
    agents.plan = lambda team, todo, feedback=None, devs=None: [
        {"task_id": "T-002", "split": "web and API. Proposed parts: T-002a backend, T-002b web"}
    ]
    team, graph = make(repo, agents)
    graph.invoke({}, {"configurable": {"thread_id": "r"}})
    t = task(team, "T-002")
    assert t["status"] == "blocked" and "T-002a backend" in t["blocked_reason"]


# --- 7. devteam stop <task> / resume <task> --------------------------------------------------
def test_stop_and_resume_a_task_with_no_run_active(repo, capsys):
    team, _ = make(repo)
    cli.control_task(team, "stop", "T-001")
    t = task(team, "T-001")
    assert t["status"] == "blocked" and t["blocked_reason"].startswith(GR.OWNER_STOP)
    assert not team.control_file("stop", "T-001").exists()  # applied
    cli.control_task(team, "resume", "T-001")
    assert task(team, "T-001")["status"] == "todo" and "blocked_reason" not in task(team, "T-001")
    out = capsys.readouterr().out
    assert "T-001: blocked, stopped by the owner" in out and "T-001: back to todo" in out
    log = git(remote_of(repo), "log", "--format=%s", "dev")
    assert "backlog: T-001 todo (resumed by the owner)" in log
    with pytest.raises(SystemExit):
        cli.control_task(team, "stop", "NOPE")


class Sleeper(Fast):
    """T-001's round runs a long command; T-002 is normal."""

    def develop(self, team, s):
        if s["task"]["id"] == "T-001":
            team.run_proc("sleep 60", cwd=s["worktree"], timeout=120, agent=s["assignee"])
        return super().develop(team, s)


def test_stop_one_task_while_it_runs_the_others_finish(repo):
    team, graph = make(repo, Sleeper())
    cont = C.Continuous(team, graph)

    def stop_soon():  # once T-001's round is running
        for _ in range(400):
            if "building the plan" in _events(team, "T-001"):
                break
            time.sleep(0.05)
        time.sleep(0.5)
        team.request("stop", "T-001")

    threading.Thread(target=stop_soon, daemon=True).start()
    t0 = time.monotonic()
    s = cont.run()
    assert time.monotonic() - t0 < 50  # the 60 s step was killed
    assert statuses(team) == {"T-001": "blocked", "T-002": "done", "T-003": "done"}
    assert task(team, "T-001")["blocked_reason"].startswith(GR.OWNER_STOP)
    assert not team.control_file("stop", "T-001").exists()  # applied: resume starts clean
    assert s["stop_reason"] == "no plannable task left for any developer"


def _events(team, tid) -> str:
    log = team.home / "events.log"
    if not log.exists():
        return ""
    try:
        events = yaml.safe_load(log.read_text()) or []
    except yaml.YAMLError:  # a write in progress
        return ""
    return "".join(e["msg"] for e in events if e["task"] == tid)


# --- 8. dashboard: agents only from the current run ------------------------------------------
def ev(agent, status, task, ts):
    return {"agent": agent, "msg": "m", "status": status, "t": ts[11:], "task": task, "to": "",
            "ts": ts}  # fmt: skip


def test_dashboard_agents_come_only_from_the_current_run(repo):
    team, _ = make(repo)
    now = time.mktime(time.strptime("2026-10-17T12:00:00", "%Y-%m-%dT%H:%M:%S"))
    (team.home / "run.pid").write_text(str(os.getpid()))
    os.utime(team.home / "run.pid", (now - 600, now - 600))  # this run started 11:50
    team.update_task("T-001", status="in_progress")
    team.update_task("T-002", status="done")
    with open(team.home / "events.log", "w") as f:
        for e in [
            ev("dev_pipeline", "working", "P0", "2026-10-16T09:00:00"),  # an old run's last word
            ev("dev_app", "working", "A2a", "2026-10-17T11:00:00"),
            ev("dev_app", "working", "T-002", "2026-10-17T11:55:00"),  # done in the backlog
            ev("dev_pipeline", "working", "T-001", "2026-10-17T11:56:00"),
        ]:
            f.write(yaml.safe_dump([e]))
    d = D.collect(team, None, now=now)
    assert [x["task"] for x in d["agents"]["dev_pipeline"]] == ["T-001"]
    assert d["agents"]["dev_app"] == []


# --- 9. prompt caching ---------------------------------------------------------------------------
class CachingClient:
    def __init__(self):
        self.kw, self.messages = None, self

    def stream(self, **kw):
        self.kw = kw
        usage = SimpleNamespace(
            input_tokens=100,
            output_tokens=10,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=9000,
        )
        msg = SimpleNamespace(
            usage=usage,
            stop_reason="end_turn",
            content=[SimpleNamespace(type="text", text='{"ok": true}')],
        )
        return _Ctx(msg)


class _Ctx:
    def __init__(self, msg):
        self.msg = msg

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self.msg


def test_brain_and_tester_calls_cache_the_stable_prefix_and_log_hits(repo):
    team, _ = make(repo)
    client = CachingClient()
    llm_call(team, "brain", "T-001", "rules", "open tasks", {}, client=client, context="CLAUDE.md")
    kw = client.kw
    assert kw["system"] == [
        {"type": "text", "text": "rules", "cache_control": {"type": "ephemeral", "ttl": "1h"}}
    ]
    content = kw["messages"][0]["content"]
    assert content[0] == {
        "type": "text",
        "text": "CLAUDE.md",
        "cache_control": {"type": "ephemeral", "ttl": "1h"},
    }
    assert content[-1] == {"type": "text", "text": "open tasks"}  # the varying part comes last
    (row,) = costs.day_rows(team.home, costs._today())
    agent, _m, _n, usd, billed, tokens_in, read, wrote = row
    assert (agent, billed, tokens_in, read, wrote) == ("brain", 1, 9100, 9000, 0)
    assert usd == pytest.approx((100 * 4.0 + 9000 * 0.2 + 10 * 20.0) / 1e6)
    html = D.render(D.collect(team, None))
    assert "Prompt cache hits" in html and "99 % (9,000 tok)" in html


# --- P3e: a task may add data under `writable`, never change what is there ---------------------
class WritesRuns(Fast):
    def __init__(self, change):
        self.change = change

    def develop(self, team, s):
        runs = team.root / "data" / "runs"
        (runs / "new-run").mkdir(parents=True, exist_ok=True)
        (runs / "new-run" / "manifest.json").write_text("{}")
        if self.change:
            (runs / "old-run" / "manifest.json").write_text('{"changed": true}')
        return super().develop(team, s)


@pytest.mark.parametrize("change", [False, True])
def test_writable_data_folder_may_only_grow(repo, change):
    (repo / "data" / "runs" / "old-run").mkdir(parents=True)
    (repo / "data" / "runs" / "old-run" / "manifest.json").write_text("{}")
    agents = WritesRuns(change)
    agents.plan = lambda team, todo, feedback=None, devs=None: [plan("T-001", "dev_pipeline")]
    team, graph = make(repo, agents)
    team.update_task("T-001", writable=["data/runs"])
    graph.invoke({}, {"configurable": {"thread_id": "r"}})
    t = task(team, "T-001")
    if change:
        assert t["status"] == "blocked"
        assert "changed or deleted 1 existing file(s)" in t["blocked_reason"]
        assert "data/runs/old-run/manifest.json" in t["blocked_reason"]
    else:
        assert t["status"] == "done"


# --- 5, 6, 10: the real backlog -----------------------------------------------------------------
def test_real_backlog_split_a7_p3e_and_order():
    tasks = yaml.safe_load((HERE / "backlog.yaml").read_text())["tasks"]
    by = {t["id"]: t for t in tasks}
    for t in tasks:  # every dependency exists
        assert set(t.get("depends_on") or []) <= set(by), t["id"]
    assert by["A7a"]["area"] == "core" and "backend/" in by["A7a"]["goal"]
    assert by["A7"]["depends_on"] == ["A7a"] and by["A7"]["branch"] == "devteam/A7"
    assert by["A7"]["status"] == "blocked"  # back to todo when A7a merges (unblock_ready)
    assert by["P3e"]["status"] == "todo" and by["P3e"]["writable"] == ["data/runs"]
    assert "P4a-2" not in (by["S1"].get("depends_on") or []) and by["S1"]["status"] == "todo"
    assert by["U2"]["area"] == "app" and by["U2"]["depends_on"] == ["S1"]
    assert by["D2"]["area"] == "core" and by["D2"]["depends_on"] == ["S1", "U2"]
    assert "U2" in by["A8"]["depends_on"]  # no new viewer features before upload
    core_todo = [
        t["id"] for t in tasks if t["area"] == "core" and t["status"] in ("todo", "blocked")
    ]
    assert core_todo[:5] == ["P3e", "P5a", "D1", "S1", "A7a"]
    app_todo = [t["id"] for t in tasks if t["area"] == "app" and t["status"] == "todo"]
    assert app_todo[:2] == ["O1", "U2"]
    # A7a's own criteria-shaped goal stays inside dev_pipeline's folders
    assert outside_paths([by["A7a"]["goal"]], "dev_pipeline", CFG, by["A7a"]) == []
