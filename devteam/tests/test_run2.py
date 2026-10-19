"""Owner, 2026-10-18, after run cont-20261018-153430: dev CI red from a status test, an e2e gate
in the tester (A5-fix), P3e killed while COLMAP worked, the O1 split, a blocked streak pauses
one developer instead of the run, quiet dashboard on closed connections."""

import copy
import io
import json
import os
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.request
from contextlib import redirect_stderr
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from conftest import CFG, HERE, git, real_backlog, remote_of
from langgraph.checkpoint.sqlite import SqliteSaver

from devteam import continuous as C
from devteam import costs, procs
from devteam import dashboard as D
from devteam import graph as GR
from devteam import supervise as SV
from devteam.agents import FakeAgents
from devteam.graph import Team, build_graph
from devteam.real import RealAgents, e2e_rule
from devteam.supervise import TaskHalted


class Fast(FakeAgents):
    delay = 0


@pytest.fixture(autouse=True)
def fast_poll(monkeypatch):
    monkeypatch.setattr(GR, "POLL_S", 0.1)


def make(repo, agents=None, fake=True, **cfg_over):
    cfg = copy.deepcopy(CFG)
    cfg.update(cfg_over)
    team = Team(repo, cfg, {}, agents or Fast(), fake=fake, remote=remote_of(repo))
    graph = build_graph(team, SqliteSaver(sqlite3.connect(":memory:", check_same_thread=False)))
    return team, graph


def task(team, tid):
    return next(t for t in team.load_backlog() if t["id"] == tid)


def only(tid, dev):
    def plan(team, todo, feedback=None, devs=None):
        return [{"task_id": tid, "assignee": dev, "plan": "p", "criteria": ["stub file exists"]}]

    return plan


# --- 1. dev CI: the real-backlog test survives every status Kerege writes -----------------------
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


# --- 2. e2e gate --------------------------------------------------------------------------------
E2E = (
    "{py} -c \"import os; open({log!r}, 'w').write(os.getcwd() + ' {port}')\" && "
    "({py} -m http.server {port} --bind 127.0.0.1 >/dev/null 2>&1 &) && sleep 1 && "
    "echo '  ✓  1 [chromium] › e2e/far-layer.spec.ts:9:1 › malformed far arrays warn (2.0s)' && "
    "echo '  -  2 [chromium] › e2e/viewer.spec.ts:5:1 › real bundle (skipped)' && "
    "echo '  1 passed, 1 skipped' && exit {code}"
)


def e2e_team(repo, tmp_path, code=0, dev="dev_app"):
    agents = Fast()
    agents.plan = only("T-002" if dev == "dev_app" else "T-001", dev)
    devs = copy.deepcopy(CFG["developers"])
    for d in devs.values():
        d.update(setup=[], checks=[])
        d.pop("screenshots", None)
    gates = copy.deepcopy(CFG["gates"])
    log = tmp_path / "e2e.log"
    gates["e2e"]["command"] = E2E.format(py=sys.executable, log=str(log), port="{port}", code=code)
    team, graph = make(repo, agents, fake=False, developers=devs, gates=gates, max_fix_loops=0)
    graph.invoke({}, {"configurable": {"thread_id": "r"}})
    return team, log


def port_open(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


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


def test_failed_e2e_sends_the_task_back_with_each_test_line(repo, tmp_path):
    team, _ = e2e_team(repo, tmp_path, code=1)
    t = task(team, "T-002")
    assert t["status"] == "blocked"
    packet = (team.home / "packets" / "T-002.md").read_text()
    assert "- FAIL: `" in packet and "http.server" in packet


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
    assert "run by the tester, not inside your sandbox" in e2e_rule(
        team, CFG["developers"]["dev_app"]
    )
    assert e2e_rule(team, CFG["developers"]["dev_pipeline"]) == ""
    a5 = next(t for t in real_backlog() if t["id"] == "A5-fix")
    assert "e2e gate" in a5["goal"] and "runs in CI" not in a5["goal"]


# --- 3. supervisor: progress, per-task limits, output tokens --------------------------------
def test_round_watch_counts_cpu_work_as_progress():
    lim = {**SV.DEFAULTS, "max_idle_min": 1, "max_round_min": 10}
    counter = SimpleNamespace(feed=lambda chunk: 0)
    w = SV.RoundWatch(counter, 0, lim)
    assert w(b"", 0, {1: 0.0}) == ""
    assert w(b"", 59, {1: 1.0}) == ""
    assert w(b"", 61, {1: 60.0}) == ""  # COLMAP at full speed, no output: working
    assert w(b"", 100, None) == ""  # between CPU samples
    assert w(b"", 120, {1: 60.5}) == ""  # 59 s since the last progress: not yet
    assert "no progress for 1 min" in w(b"", 122, {1: 60.5})
    w = SV.RoundWatch(counter, 0, lim)
    assert w(b'{"type":"x"}\n', 70, {1: 0.0}) == "" and w(b"", 125, None) == ""  # output counts
    # a child that started since the last sample counts with all its CPU
    w = SV.RoundWatch(counter, 0, lim)
    w(b"", 0, {1: 0.0})
    assert w(b"", 61, {1: 0.0, 2: 5.0}) == "" and w.active_at == 61
    assert "the round ran 10 min (limit 10 min per round" in w(b"x", 600, None)


def test_a_task_sets_its_own_round_limit():
    cfg = {"supervisor": {"max_round_min": 60}}
    assert SV.limits(cfg)["max_round_min"] == 60 and SV.limits(cfg)["max_idle_min"] == 20
    p3e = next(t for t in real_backlog() if t["id"] == "P3e")
    assert SV.limits(cfg, p3e)["max_round_min"] == 180
    assert SV.limits(cfg, p3e)["max_tokens_per_task"] == SV.DEFAULTS["max_tokens_per_task"]


def test_cpu_times_of_a_step():
    assert procs._cpu_s("0:01.50") == 1.5  # BSD
    assert procs._cpu_s("00:01:02") == 62  # procps
    assert procs._cpu_s("1-00:00:01") == 86401
    busy = "import time\nt = time.time()\nwhile time.time() - t < 3: pass"
    p = subprocess.Popen([sys.executable, "-c", busy], start_new_session=True)
    try:
        time.sleep(1.5)
        cpu = procs.cpu_times(p.pid, "no-marker")
        assert p.pid in cpu and cpu[p.pid] > 0.5
    finally:
        p.kill()
        p.wait()


@pytest.mark.parametrize("busy", [True, False])
def test_a_step_doing_cpu_work_is_not_killed_an_idle_one_is(repo, monkeypatch, busy):
    monkeypatch.setattr(GR, "CPU_EVERY_S", 0.3)
    monkeypatch.setattr(SV, "CPU_ACTIVE_S", 0.1)
    team, _ = make(repo)
    lim = {**SV.DEFAULTS, "max_idle_min": 1.5 / 60}  # 1.5 s without progress
    watch = SV.RoundWatch(SimpleNamespace(feed=lambda chunk: 0), 0, lim)
    loop = "import time\nt = time.time()\nwhile time.time() - t < 4: pass"
    cmd = [sys.executable, "-c", loop] if busy else ["sleep", "4"]
    if busy:
        r = team.run_proc(cmd, cwd=str(repo), timeout=30, watch=watch)
        assert r["ok"]
    else:
        with pytest.raises(TaskHalted, match="no progress for 0 min"):
            team.run_proc(cmd, cwd=str(repo), timeout=30, watch=watch)


FAKE_CLAUDE = """#!{py}
import json, sys, time
sys.stdin.read()
print(json.dumps({{"type": "system", "subtype": "init", "session_id": "sess-1"}}), flush=True)
for mid in ("m1", "m2"):  # usage as of each message's start: 3 output tokens
    u = {{"input_tokens": 2000, "output_tokens": 3}}
    print(json.dumps({{"type": "assistant", "message": {{"id": mid, "usage": u}}}}), flush=True)
time.sleep(60)
"""


def test_a_killed_claude_round_records_output_tokens_from_its_transcript(
    repo, tmp_path, monkeypatch
):
    """P3e: 5,597,007 in / 0 out in the report; the transcript had 27,543 output tokens."""
    bindir, home = tmp_path / "bin", tmp_path / "claude-home"
    bindir.mkdir()
    (bindir / "claude").write_text(FAKE_CLAUDE.format(py=sys.executable))
    (bindir / "claude").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}:{bindir.parent}:{__import__('os').environ['PATH']}")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    proj = home / "projects" / "-tmp-wt-T-001"
    proj.mkdir(parents=True)
    rows = [  # final counts; m0 is from an earlier round of the resumed session
        {
            "type": "assistant",
            "message": {"id": i, "usage": {"input_tokens": 2000, "output_tokens": o}},
        }
        for i, o in (("m0", 999), ("m1", 400), ("m2", 300))
    ]
    (proj / "sess-1.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    team, _ = make(repo, supervisor={"max_tokens_per_round": 3000})
    team.secrets = {"CLAUDE_CODE_OAUTH_TOKEN": "oauth-0123456789"}
    s = {
        "task": {"id": "T-001", "title": "t", "goal": "g"},
        "assignee": "dev_pipeline",
        "plan": "p",
        "criteria": ["c"],
        "worktree": str(repo),
    }
    with pytest.raises(TaskHalted, match="this round used"):
        RealAgents().develop(team, s)
    (row,) = costs.subscription_rows(team.home, costs._today())
    assert (row[5], row[6]) == (4000, 700)


def test_codex_counter_keeps_input_and_output_apart(tmp_path):
    day = tmp_path / "sessions" / "2026" / "10" / "18"
    day.mkdir(parents=True)
    rollout = day / "rollout-2026-10-18T10-00-00-th-1.jsonl"

    def count(i, o):
        u = {"input_tokens": i, "output_tokens": o}
        e = {"payload": {"type": "token_count", "info": {"total_token_usage": u}}}
        with open(rollout, "a") as f:
            f.write(json.dumps(e) + "\n")

    count(1000, 10)
    c = SV.CodexTokens("th-1", home=tmp_path)  # resumed: this round starts at 1000 / 10
    count(4000, 90)
    assert c.feed(b"") == 3080 and c.usage() == {"in": 3000, "out": 80}


# --- 4. O1 split ----------------------------------------------------------------------------
@pytest.mark.backlog
def test_o1_is_split_into_a_backend_task_and_the_web_page():
    by = {t["id"]: t for t in real_backlog()}
    o1a, o1 = by["O1a"], by["O1"]
    assert (
        o1a["area"] == "core" and "backend/" in o1a["goal"] and "Syracuse University" in o1a["goal"]
    )
    assert "require_role" in o1a["goal"] and "Alembic" in o1a["goal"]
    assert o1["area"] == "app" and o1["depends_on"] == ["O1a"]
    core = [t["id"] for t in by.values() if t["area"] == "core"]
    assert core.index("O1a") == core.index("S1") + 1
    assert GR.outside_paths([o1a["goal"]], "dev_pipeline", CFG, o1a) == []
    assert GR.outside_paths([o1["goal"]], "dev_app", CFG, o1) == []


# --- 5. a blocked streak pauses one developer, not the run ------------------------------------
class BlockCore(Fast):
    """The tester fails every dev_pipeline task; dev_app's pass at once."""

    def judge(self, team, s, checks):
        v = super().judge(team, s, checks)
        if s["assignee"] == "dev_app":
            v.update(passed=True, feedback="")
            v["criteria"] = [{**c, "met": True} for c in v["criteria"]]
        return v


def test_two_blocked_in_a_row_pause_that_developer_the_others_go_on(repo):
    team, graph = make(repo, BlockCore(), max_fix_loops=0)
    data = yaml.safe_load(team.backlog_path.read_text())
    data["tasks"] += [
        {"id": "T-004", "area": "core", "title": "core 4", "goal": "g", "status": "todo"},
        {"id": "T-005", "area": "app", "title": "app 5", "goal": "g", "status": "todo"},
        {"id": "T-006", "area": "app", "title": "app 6", "goal": "g", "status": "todo"},
    ]
    team.backlog_path.write_text(yaml.safe_dump(data, sort_keys=False))
    s = C.Continuous(team, graph).run()
    st = {t["id"]: t["status"] for t in team.load_backlog()}
    assert st == {
        "T-001": "blocked",
        "T-002": "done",
        "T-003": "blocked",
        "T-004": "todo",  # dev_pipeline paused before it
        "T-005": "done",
        "T-006": "done",  # dev_app kept going
    }
    assert s["stop_reason"] == (
        "no developer can take a task: dev_pipeline paused: 2 of its tasks in a row ended "
        "blocked (T-001, T-003); no plannable task left for the others"
    )
    assert "dev_pipeline paused" in (team.home / "events.log").read_text()


# --- 6. dashboard: a closed connection is not an error --------------------------------------
def test_dashboard_is_quiet_when_the_browser_drops_the_connection(repo):
    team, _ = make(repo)
    server = D.make_server(team, 0)
    err = io.StringIO()
    with redirect_stderr(err):
        for exc in (ConnectionResetError(54, "reset"), BrokenPipeError(32, "pipe")):
            try:
                raise exc
            except ConnectionError:
                server.handle_error(None, ("127.0.0.1", 1))
        assert err.getvalue() == ""
        try:
            raise ValueError("a real bug")
        except ValueError:
            server.handle_error(None, ("127.0.0.1", 1))
    assert "ValueError: a real bug" in err.getvalue()  # real errors still show
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/"
        assert urllib.request.urlopen(url, timeout=30).status == 200
    finally:
        server.shutdown()
        server.server_close()
