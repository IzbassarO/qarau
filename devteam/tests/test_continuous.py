"""Continuous mode (Kerege): per-developer planning, stop conditions, limit waits, clean stop."""

import copy
import json
import os
import sqlite3
import time

import pytest
from conftest import CFG, git, remote_of
from langgraph.checkpoint.sqlite import SqliteSaver
from test_v2 import CODEX_FAILED

from devteam import continuous as C
from devteam import costs
from devteam.agents import FakeAgents
from devteam.graph import Team, build_graph
from devteam.real import RealAgents


class Fast(FakeAgents):
    delay = 0


def make(repo, agents=None, **cfg_over):
    cfg = copy.deepcopy(CFG)
    cfg.update(cfg_over)
    team = Team(repo, cfg, {}, agents or Fast(), fake=True, remote=remote_of(repo))
    db = sqlite3.connect(team.home / "c.sqlite", check_same_thread=False)
    return team, build_graph(team, SqliteSaver(db))


def statuses(team):
    return {t["id"]: t["status"] for t in team.load_backlog()}


def test_each_developer_gets_its_next_task_until_nothing_is_left(repo):
    team, graph = make(repo)
    main_before = git(remote_of(repo), "rev-parse", "main")
    s = C.Continuous(team, graph).run()
    assert statuses(team) == {"T-001": "done", "T-002": "done", "T-003": "done"}
    assert [(r["id"], r["dev"]) for r in s["tasks"] if r["dev"] == "dev_pipeline"] == [
        ("T-001", "dev_pipeline"),  # planned again for dev_pipeline only after T-001 ended
        ("T-003", "dev_pipeline"),
    ]
    assert s["stop_reason"] == "no plannable task left for any developer"
    assert git(remote_of(repo), "rev-parse", "main") == main_before  # never a release
    saved = json.loads((team.home / "runs" / f"{s['run']}.json").read_text())
    assert saved["stop_reason"] == s["stop_reason"]
    text = "\n".join(C.summary_lines(saved))
    assert "| T-003 | dev_pipeline | done | fake://pr/" in text
    assert "Stopped because: **no plannable task left for any developer**" in text
    assert not C.Continuous(team, graph).pending()  # nothing left to resume


def test_max_tasks_stops_taking_new_tasks(repo):
    team, graph = make(repo, continuous={"max_tasks": 1})
    s = C.Continuous(team, graph).run()
    assert len(s["tasks"]) == 1 and s["tasks"][0]["status"] == "done"
    assert s["stop_reason"] == "max_tasks reached (1)"
    assert sorted(statuses(team).values()) == ["done", "todo", "todo"]


def test_two_blocked_tasks_in_a_row_stop_the_run(repo):
    team, graph = make(repo, max_fix_loops=0)  # the fake tester fails round 1: blocked
    s = C.Continuous(team, graph).run()
    assert [r["status"] for r in s["tasks"]] == ["blocked", "blocked"]
    assert s["stop_reason"].startswith("2 tasks in a row ended blocked")
    # T-003 may have started in between; the stop leaves it to resume, never finished
    assert statuses(team)["T-003"] in ("todo", "in_progress")


def test_budget_reached_stops_before_any_task(repo):
    team, graph = make(repo, daily_budget_usd=0.0)
    s = C.Continuous(team, graph).run()
    assert s["tasks"] == [] and s["stop_reason"].startswith("daily API budget reached")


def test_max_hours_stops_before_new_tasks(repo):
    team, graph = make(repo, continuous={"max_hours": 0})
    s = C.Continuous(team, graph).run()
    assert s["tasks"] == [] and s["stop_reason"] == "max_hours reached (0 h)"


class LimitOnce(Fast):
    hit = False

    def develop(self, team, s):
        if not LimitOnce.hit:
            LimitOnce.hit = True
            raise costs.SubscriptionLimit("codex failed: usage limit reached", time.time() - 99)
        return super().develop(team, s)


def test_subscription_limit_is_logged_waited_out_and_the_task_continues(repo, monkeypatch):
    monkeypatch.setattr(C, "MIN_WAIT_S", 0)
    team, graph = make(repo, LimitOnce())
    s = C.Continuous(team, graph).run()
    assert sorted(statuses(team).values()) == ["done", "done", "done"]
    (row,) = costs.limit_rows(team.home, costs._today())
    assert row[1] in ("dev_pipeline", "dev_app") and row[2] in ("T-001", "T-002")
    assert "usage limit" in row[5]
    assert s["stop_reason"] == "no plannable task left for any developer"


def test_limit_wait_is_capped(repo, monkeypatch):
    monkeypatch.setattr(C, "MIN_WAIT_S", 0)
    LimitOnce.hit = False
    team, graph = make(repo, LimitOnce(), continuous={"limit_wait_max_hours": 0})
    s = C.Continuous(team, graph).run()
    assert "subscription limit did not reset" in s["stop_reason"]


class StopDuringDevelop(Fast):
    cont = None
    calls: list[str] = []

    def develop(self, team, s):
        StopDuringDevelop.calls.append(s["task"]["id"])
        if len(StopDuringDevelop.calls) == 1:
            StopDuringDevelop.cont.halt("Ctrl+C")  # pressed while the developer works
        return super().develop(team, s)


def test_ctrl_c_finishes_the_step_and_the_next_run_resumes(repo):
    StopDuringDevelop.calls = []
    team, graph = make(repo, StopDuringDevelop())
    cont = StopDuringDevelop.cont = C.Continuous(team, graph)
    s = cont.run()
    assert s["stop_reason"] == "Ctrl+C"
    pending = cont.pending()
    assert pending  # the round(s) in flight, kept for the next run
    claimed = cont.ignored_drift()
    assert claimed and all(statuses(team)[t] == "in_progress" for t in claimed)
    first = StopDuringDevelop.calls[0]
    log = git(repo, "log", "--oneline", f"devteam-fake/{first}")
    assert "(round 1)" in log  # the step that was running finished and was committed

    team.stopping.clear()
    cont2 = StopDuringDevelop.cont = C.Continuous(team, graph)
    s2 = cont2.run()
    assert statuses(team) == {"T-001": "done", "T-002": "done", "T-003": "done"}, (
        s,
        pending,
        s2,
    )
    assert s2["stop_reason"] == "no plannable task left for any developer"
    # the interrupted task was not started over: 2 developer rounds each (fails, then passes)
    assert StopDuringDevelop.calls.count(first) == 2


def test_continuous_refuses_a_developer_that_owns_devteam(repo):
    team, graph = make(repo)
    team.cfg["developers"]["dev_app"]["owns"] = ["web/", "devteam/"]
    with pytest.raises(ValueError, match="owns devteam"):
        C.Continuous(team, graph)


@pytest.mark.parametrize(
    ("text", "hit", "reset"),
    [
        ("Claude AI usage limit reached|1760000000", True, "1760000000"),
        ("5-hour limit reached ∙ resets 3pm", True, "15:00"),
        ("You've hit your limit · resets 3:30pm (America/New_York)", True, "15:30"),
        ("You've hit your usage limit. Try again in 2 hours 5 minutes.", True, "12:05"),
        ("usage limit reached, try again at 9:05 AM", True, "09:05 +1d"),
        ("usage limit reached", True, None),
        ("tests failed: 3 errors", False, None),
    ],
)
def test_limit_reset_parsing(text, hit, reset, monkeypatch):
    monkeypatch.setenv("TZ", "America/New_York")
    time.tzset()
    now = time.mktime((2026, 10, 17, 10, 0, 0, 0, 0, -1))
    got_hit, at = costs.limit_reset(text, now)
    assert got_hit == hit
    if reset is None:
        assert at is None
    elif reset.isdigit():
        assert at == float(reset)
    else:
        hm, _, plus = reset.partition(" ")
        day = 18 if plus else 17
        assert time.strftime("%d %H:%M", time.localtime(at)) == f"{day} {hm}"


def test_codex_limit_raises_subscription_limit(repo, tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "codex").write_text(
        "#!/bin/sh\ncat > /dev/null\n"
        f'[ -n "$RUNPOD_API_KEY" ] && echo leaked > {tmp_path}/key\n'
        f"cat <<'EOF'\n{CODEX_FAILED}EOF\n"
    )
    (bindir / "codex").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
    monkeypatch.setenv("RUNPOD_API_KEY", "rp-should-not-pass")
    team, _ = make(repo)
    s = {
        "task": {"id": "T-002", "title": "t", "goal": "g"},
        "assignee": "dev_app",
        "plan": "p",
        "criteria": ["c"],
        "worktree": str(repo),
    }
    with pytest.raises(costs.SubscriptionLimit, match="usage limit"):
        RealAgents().develop(team, s)
    assert not (tmp_path / "key").exists()


def test_paid_service_keys_never_reach_developers():
    from devteam.real import paid_service_key

    assert paid_service_key("RUNPOD_API_KEY") and paid_service_key("AWS_SECRET_ACCESS_KEY")
    assert paid_service_key("MODAL_TOKEN_ID") and paid_service_key("MODAL_TOKEN_SECRET")
    assert not paid_service_key("PATH") and not paid_service_key("HOME")
