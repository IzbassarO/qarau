"""Kerege dashboard: real data only, read-only, local, never shows secrets."""

import copy
import json
import os
import threading
import time
import urllib.error
import urllib.request

import pytest
import yaml
from conftest import CFG, remote_of

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
    monkeypatch.setattr(costs, "spent_today", lambda home: 2.5)
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
    kinds = [(k, t) for k, t, _u in d["attention"]]
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
