"""Kerege dashboard: real data only, read-only, local, never shows secrets."""

import copy
import io
import json
import os
import threading
import time
import urllib.error
import urllib.request
from contextlib import redirect_stderr

import pytest
import yaml
from conftest import CFG, remote_of, team_for

from devteam import costs
from devteam import dashboard as D
from devteam.agents import FakeAgents
from devteam.graph import Team

SECRET = "sk-ant-oat01-very-secret-token"


@pytest.fixture
def team(repo):
    cfg = copy.deepcopy(CFG)
    t = Team(repo, cfg, {"CLAUDE_CODE_OAUTH_TOKEN": SECRET}, FakeAgents(), remote=remote_of(repo))
    return t


def fake_gh(prs, runs):
    def run(*args):
        return json.dumps(prs if args[0] == "pr" else runs)

    return D.GhCache(None, runner=run)


def write_events(team, events):
    with open(team.home / "events.log", "a") as f:
        for e in events:
            f.write(yaml.safe_dump([e], allow_unicode=True, width=1000))


def ev(agent, status, task, ts, msg="m"):
    return {"agent": agent, "msg": msg, "status": status, "t": ts[11:], "task": task, "to": "",
            "ts": ts}  # fmt: skip


def test_collect_shows_agents_spend_limits_queue_and_attention(team, monkeypatch):
    now = time.mktime(time.strptime("2026-10-17T12:00:00", "%Y-%m-%dT%H:%M:%S"))
    (team.home / "run.pid").write_text(str(os.getpid()))  # a live run, started at 11:00
    os.utime(team.home / "run.pid", (now - 3600, now - 3600))
    write_events(
        team,
        [
            ev("dev_pipeline", "working", "T-001", "2026-10-17T11:15:00", "building the plan"),
            ev("dev_app", "working", "T-002", "2026-10-17T11:00:00"),
            ev("dev_app", "idle", "T-002", "2026-10-17T11:30:00"),
            ev("dev_app", "waiting", "T-004", "2026-10-17T11:50:00", "subscription limit hit"),
        ],
    )
    monkeypatch.setattr(costs, "_today", lambda: "2026-10-17")
    monkeypatch.setattr(costs, "spent_24h", lambda home: 2.5)
    costs.record(team.home, "tester", "m", "T-001", 10, 5, 2.5, billed=True)
    costs.record(team.home, "dev_app", "codex (subscription)", "T-002", 900, 40, 0, False, 600)
    costs.record_limit(team.home, "dev_app", "T-004", now + 3600, 3660, "usage limit reached")
    team.update_task("T-001", status="in_progress", owner="dev_pipeline")
    team.update_task("T-003", status="blocked", blocked_reason="Waits for the owner: GPU")
    team.update_task("T-002", status="done", pr="https://github.com/o/r/pull/7")
    team.cfg["daily_budget_usd"] = 3.0
    prs = [
        {"number": 30, "title": "Kerege PR", "url": "https://github.com/o/r/pull/30",
         "headRefName": "feature/kerege", "baseRefName": "dev"},
        {"number": 31, "title": "T-001 work", "url": "https://github.com/o/r/pull/31",
         "headRefName": "devteam/T-001", "baseRefName": "dev"},
    ]  # fmt: skip
    runs = [
        {"workflowName": "CI", "status": "completed", "conclusion": "failure",
         "url": "https://github.com/o/r/actions/runs/1", "headSha": "abcdef1234"},
        {"workflowName": "CI", "status": "completed", "conclusion": "success",
         "url": "https://github.com/o/r/actions/runs/0", "headSha": "0000000000"},
    ]  # fmt: skip
    team.fake = False  # real mode reads gh (here a stand-in)
    d = D.collect(team, fake_gh(prs, runs), now=now)

    assert d["active"]
    (pipe,) = d["agents"]["dev_pipeline"]
    assert (pipe["task"], pipe["state"], pipe["secs"]) == ("T-001", "working", 45 * 60)
    (app,) = d["agents"]["dev_app"]  # T-002 went idle, T-004 waits for a limit reset
    assert (app["task"], app["state"]) == ("T-004", "waiting")
    assert d["agents"]["tester"] == []
    assert d["limits"][0][1:4] == ("dev_app", "T-004", now + 3600)
    kinds = [(a["kind"], a["text"]) for a in d["attention"]]
    assert ("CI", "CI on dev is failure (abcdef12)") in kinds  # newest completed run
    assert any(k == "blocked" and "T-003" in t for k, t in kinds)
    assert any(k == "PR" and "#30" in t for k, t in kinds)  # waits for the owner
    assert not any("#31" in t for _k, t in kinds)  # devteam is still on T-001
    assert any(k == "spend" for k, _t in kinds)  # 2.50 > 80 % of 3.00

    html = D.render(d)
    assert "PR #7" in html and "waiting for limit reset" in html and "45 min" in html


def test_no_active_run_means_every_agent_idle(team):
    write_events(team, [ev("dev_app", "working", "T-002", "2026-10-17T11:00:00")])
    d = D.collect(team, fake_gh([], []))
    assert all(v == [] for v in d["agents"].values())
    assert not d["active"]


def test_page_escapes_text_and_redacts_secrets(team):
    team.update_task("T-001", status="blocked", blocked_reason=f"<script>x</script> {SECRET}")
    write_events(team, [ev("dev_app", "idle", "T-002", "2026-10-17T11:00:00", SECRET)])
    page = D.page(team, fake_gh([], []))
    assert "<script>x" not in page and "&lt;script&gt;" in page
    assert SECRET not in page and "***" in page


def test_server_is_local_and_read_only(team):
    server = D.make_server(team, 0, fake_gh([], []))
    assert server.server_address[0] == "127.0.0.1"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}"
    try:
        body = urllib.request.urlopen(url + "/").read().decode()
        assert "<title>Kerege dashboard</title>" in body and "Needs your attention" in body
        for path, method in (("/x", "GET"), ("/", "POST"), ("/", "DELETE")):
            with pytest.raises(urllib.error.HTTPError) as e:
                urllib.request.urlopen(urllib.request.Request(url + path, method=method))
            assert e.value.code in (404, 501)
    finally:
        server.shutdown()


def test_gh_failure_is_shown_not_fatal(team):
    def broken(*args):
        raise RuntimeError("gh: not logged in")

    team.fake = False
    d = D.collect(team, D.GhCache(team, runner=broken))
    assert d["prs"] == {"error": "gh: not logged in"}
    assert "gh: not logged in" in D.render(d)


# --- v2 (owner, 2026-10-17): summary, actions, cards, columns, timeline, office ----------------
def test_v2_summary_actions_office_and_timeline(team, monkeypatch):
    now = time.mktime(time.strptime("2026-10-17T12:00:00", "%Y-%m-%dT%H:%M:%S"))
    (team.home / "run.pid").write_text(str(os.getpid()))
    os.utime(team.home / "run.pid", (now - 7200, now - 7200))  # running for 2 h
    monkeypatch.setattr(costs, "_today", lambda: "2026-10-17")
    write_events(
        team,
        [
            ev("dev_pipeline", "working", "T-001", "2026-10-17T10:30:00", "building the plan"),
            ev("dev_app", "working", "T-002", "2026-10-17T10:15:00"),
            ev("dev_app", "idle", "T-002", "2026-10-17T11:00:00"),
            ev("dev_app", "waiting", "T-003", "2026-10-17T11:30:00", "subscription limit hit"),
            ev("tester", "working", "T-002", "2026-10-17T11:00:00"),
            ev("tester", "idle", "T-002", "2026-10-17T11:20:00", "BLOCKED: supervisor: x"),
        ],
    )
    costs.record(
        team.home, "dev_pipeline", "claude-code (subscription)", "T-001", 3_000_000, 1, 0, False
    )
    costs.record(team.home, "dev_app", "codex (subscription)", "T-002", 9_000_000, 2, 0, False)
    costs.record_limit(team.home, "dev_app", "T-003", now + 5400, 600, "usage limit reached")
    team.update_task("T-001", status="in_progress", owner="dev_pipeline")
    team.update_task("T-003", status="in_progress", owner="dev_app")
    team.update_task(
        "T-002", status="blocked", blocked_reason="supervisor: the task used 12.0 M (limit 12 M)"
    )
    d = D.collect(team, fake_gh([], []), now=now)

    assert d["run"] == {"state": "running", "reason": "", "secs": 7200}
    assert d["sub_tokens"] == 12_000_003 and d["task_tokens"][0] == ("T-002", 9_000_002)
    (a,) = [a for a in d["attention"] if a["kind"] == "blocked"]
    assert "devteam resume T-002" in a["do"] and "supervisor" in a["do"]
    office = d["office"]
    assert office["dev_pipeline"]["state"] == "working" and office["dev_pipeline"]["room"] == "Core"
    assert office["dev_app"]["state"] == "waiting" and office["dev_app"]["reset"] == now + 5400
    assert office["tester"]["state"] == "blocked" and office["tester"]["task"] == "T-002"
    assert office["brain"]["state"] == "idle" and office["brain"]["room"] == "Lead"
    assert office["dev_app"]["tokens"] == 9_000_002
    lanes = d["timeline"]["rows"]
    assert [(s["task"], s["state"]) for s in lanes["dev_app"]] == [
        ("T-002", "working"),
        ("T-003", "waiting"),
    ]
    assert lanes["dev_app"][1]["end"] == now and lanes["dev_app"][1]["open"]

    page = D.render(d)
    for text in ("Needs your attention", "Tasks done today", "Subscription tokens today",
                 "Running for", "2 h 00 min", "Office", "Core", "App", "Testing", "Lead",
                 f"data-reset='{now + 5400:.0f}'", "Dashed line: the per-task limit"):  # fmt: skip
        assert text in page, text
    # offline: nothing loaded from the network
    assert "<script src" not in page and "<link" not in page and "@import" not in page
    for name in ("dev_pipeline", "dev_app", "tester", "brain"):  # a room and a panel each
        assert f"class='room' data-who='{name}'" in page
        assert f"class='who-panel' data-who='{name}'" in page


def test_blocked_actions_tell_the_owner_what_to_do():
    by = {"A7a": {"status": "todo"}, "P1": {"status": "done"}}
    act = D.blocked_action
    assert act({"id": "A7", "depends_on": ["A7a"], "blocked_reason": "waits for A7a"}, by) is None
    assert "devteam resume X" in act({"id": "X", "blocked_reason": "stopped by the owner"}, by)
    assert "backlog.yaml" in act({"id": "X", "blocked_reason": "needs a split before it"}, by)
    assert "your decision" in act({"id": "X", "blocked_reason": "waits for owner to fund"}, by)
    assert "packets/X.md" in act({"id": "X", "blocked_reason": "tester: no tests"}, by)
    assert act({"id": "X", "depends_on": ["P1"], "blocked_reason": "tester: no"}, by)


def test_office_figures_differ_by_state():
    base = {"reset": None}
    svgs = {s: D.figure({**base, "state": s}) for s in D.STATES}
    assert len(set(svgs.values())) == 4
    assert "#e2655b" in svgs["blocked"] and "#4fb3a0" in svgs["working"]
    assert "14:30" in D.figure({"state": "waiting", "reset": time.mktime(
        time.strptime("2026-10-17T14:30:00", "%Y-%m-%dT%H:%M:%S"))})  # fmt: skip


# --- 6. dashboard: a closed connection is not an error --------------------------------------
def test_dashboard_is_quiet_when_the_browser_drops_the_connection(repo):
    team = team_for(repo)
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


# --- 4. owner-only tasks: never `devteam resume` --------------------------------------------------
def test_dashboard_never_suggests_resume_for_an_owner_only_task():
    reason = "waits for the owner: P4a-2a-code merged, then the owner runs it"
    t = {"id": "P4a-2a-run", "status": "blocked", "blocked_reason": reason}
    assert "devteam resume" in D.blocked_action(t, {})  # what it said before
    do = D.blocked_action({**t, "owner_only": True}, {})
    assert "devteam resume" not in do and "Owner-only" in do
