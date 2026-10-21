"""Owner, 2026-10-19, after runs cont-20261018-231850 / -234550: a vanished `claude` stopped the
whole run, a dev merge before a crash was blamed on the developer (P3e "protected:
backlog.yaml"), the tester's viewer read another runs dir than P3e wrote ("manifest.json not
found"), P5a split to fit the 8 M round limit."""

import copy
import json
import sqlite3
import threading
from types import SimpleNamespace

import pytest
import yaml
from conftest import CFG, HERE, git, real_backlog, remote_of
from langgraph.checkpoint.sqlite import SqliteSaver

from devteam import continuous as C
from devteam import gates as G
from devteam import graph as GR
from devteam import procs
from devteam import real as R
from devteam.agents import FakeAgents
from devteam.graph import Team, build_graph


class Fast(FakeAgents):
    delay = 0


@pytest.fixture(autouse=True)
def fast_poll(monkeypatch):
    monkeypatch.setattr(GR, "POLL_S", 0.1)


def make(repo, agents, **cfg_over):
    cfg = copy.deepcopy(CFG)
    cfg.update(cfg_over)
    team = Team(repo, cfg, {}, agents, fake=True, remote=remote_of(repo))
    graph = build_graph(team, SqliteSaver(sqlite3.connect(":memory:", check_same_thread=False)))
    return team, graph


def statuses(team):
    return {t["id"]: t["status"] for t in team.load_backlog()}


# --- 1. developer CLI: absolute path, checked before each round, one retry --------------------
def test_tool_path_is_checked_and_looked_up_again_after_a_wait(monkeypatch, tmp_path):
    monkeypatch.setattr(R, "TOOL_RETRY_S", 0)
    exe = tmp_path / "claude"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    found = iter([None, str(exe)])  # gone (mid self-update), back after the wait
    monkeypatch.setattr(R.shutil, "which", lambda name: next(found))
    agents = R.RealAgents.__new__(R.RealAgents)
    agents.paths = {"claude": str(tmp_path / "vanished")}
    team = SimpleNamespace(stopping=threading.Event())
    assert agents.tool(team, "claude") == str(exe)
    assert agents.tool(team, "claude") == str(exe)  # the resolved path is kept

    monkeypatch.setattr(R.shutil, "which", lambda name: None)
    exe.unlink()
    with pytest.raises(RuntimeError, match="`claude` not found on PATH"):
        agents.tool(team, "claude")


class CoreCrashes(Fast):
    def develop(self, team, s):
        if s["assignee"] == "dev_pipeline":
            raise FileNotFoundError(2, "No such file or directory", "claude")
        return super().develop(team, s)


def test_a_crash_pauses_only_that_developer_and_its_round_resumes_next_run(repo):
    team, graph = make(repo, CoreCrashes())
    cont = C.Continuous(team, graph)
    s = cont.run()
    st = statuses(team)
    assert st["T-002"] == "done"  # dev_app kept going
    assert st["T-001"] == "in_progress" and st["T-003"] == "todo"
    assert s["stop_reason"].startswith("no developer can take a task: dev_pipeline paused: crashed")
    assert "'claude'" in s["stop_reason"]
    pending = json.loads((team.home / "continuous.json").read_text())
    assert list(pending) == ["dev_pipeline"]  # the next run resumes this round first
    assert s["in_flight"] == {"dev_pipeline": "T-001"}


# --- 2. a dev merge made before a crash is the orchestrator's, not the developer's ------------
class MergeThenCrash(Fast):
    """Round 1: dev moves meanwhile (a status commit). Round 2: develop() merges it, then the
    agent crashes; the resumed round must not count dev's backlog.yaml as the developer's."""

    crashed = False

    def plan(self, team, todo, feedback=None, devs=None):
        return [p for p in super().plan(team, todo, feedback, devs) if p["task_id"] == "T-001"]

    def develop(self, team, s):
        if s.get("rounds", 0) == 0:
            team.update_task("T-003", status="blocked", blocked_reason="by the owner")
            team.push_status("T-003", "backlog: T-003 blocked")
        elif not MergeThenCrash.crashed:
            MergeThenCrash.crashed = True
            raise RuntimeError("crash right after the dev merge")
        return super().develop(team, s)


def test_a_dev_merge_before_a_crash_is_not_a_violation(repo):
    team, graph = make(repo, MergeThenCrash())
    cfg = {"configurable": {"thread_id": "r"}}
    with pytest.raises(RuntimeError, match="crash right after"):
        graph.invoke({}, cfg)
    wt = team.home / "worktrees" / "T-001"
    assert "Merge" in git(wt, "log", "-1", "--format=%s")  # the merge is there, unrecorded
    graph.invoke(None, cfg)
    assert statuses(team)["T-001"] == "done"
    assert not (team.home / "violations.jsonl").exists()


def test_status_push_leaves_the_main_checkout_clean(repo):
    """Each status push fast-forwards the checkout to the commit just pushed (a push by URL
    left origin/dev behind: the checkout lagged one status with backlog.yaml modified)."""
    team, _ = make(repo, Fast())
    team.fake = False  # the checkout sync runs only for real teams
    team.backlog_path = repo / "devteam" / "backlog.yaml"
    for status in ("in_progress", "blocked", "todo"):
        team.update_task("T-001", status=status)
        assert team.push_status("T-001", f"backlog: T-001 {status}")
        assert git(repo, "status", "--porcelain") == ""
    assert "status: todo" in git(repo, "show", "dev:devteam/backlog.yaml")


# --- 3. one runs dir for the pipeline, the developer and the tester --------------------------
def test_a_task_that_writes_runs_is_screenshot_on_runs_dir(repo, tmp_path):
    port = procs.free_port()
    seen = tmp_path / "served.txt"
    devs = copy.deepcopy(CFG["developers"])
    devs["dev_pipeline"]["screenshots"] = {
        "serve": f"echo {{runs}} > {seen} && exec python3 -m http.server {port} --bind 127.0.0.1",
        "url": f"http://127.0.0.1:{port}",
        "shoot": "touch {png}",
        "viewports": ["1440x900"],  # one shot per route
    }
    team, _ = make(repo, Fast(), developers=devs)
    assert team.runs_dir == repo / "data" / "runs"
    task = {"id": "T-001", "writable": ["data/runs"]}
    s = {"task": task, "assignee": "dev_pipeline", "worktree": str(repo)}
    shots, problems = G.screenshots(team, s, ["/viewer/room-2026-10-18"])
    assert problems == [] and len(shots) == 1
    assert seen.read_text().strip() == str(team.runs_dir)
    assert G.dev_screenshots(team, s, ["/viewer/room-2026-10-18"]) == ([], [])  # nothing to compare
    assert "$QARAU_RUNS_DIR" in R.writable_rule(team, task)

    other = {**s, "task": {"id": "T-003"}}  # no runs written: its quick benchmark output
    G.screenshots(team, other, ["/viewer/room"])
    assert seen.read_text().strip() == str(team.home / "bench-dev" / "T-003")
    assert "$QARAU_RUNS_DIR" not in R.writable_rule(team, other["task"])


def test_config_commands_use_the_runs_dir_setting():
    cfg = yaml.safe_load((HERE / "config.yaml").read_text())  # CFG stubs the e2e command
    assert cfg["runs_dir"] == "data/runs"
    assert "QARAU_RUNS_DIR={runs_dir}" in cfg["gates"]["e2e"]["command"]
    assert "QARAU_RUNS_DIR={runs_dir}" in cfg["developers"]["dev_app"]["screenshots"]["serve"]
    assert "{data}/runs" not in json.dumps(cfg)


# --- 4. P5a split so each part fits the 8 M round limit -------------------------------------
@pytest.mark.backlog
def test_p5a_is_split_into_distance_and_a4():
    by = {t["id"]: t for t in real_backlog()}
    one, two = by["P5a-1"], by["P5a-2"]
    assert one["area"] == two["area"] == "core"
    assert "distance" in one["goal"] and "gravity" in one["goal"] and "A4" in two["title"]
    assert two["depends_on"] == ["P5a-1"]
    assert "P5a" not in json.dumps([t.get("depends_on") for t in by.values()]).replace("P5a-", "")
    assert "P5a-1" in by["A8"]["depends_on"]
    assert "supervisor" not in one and "supervisor" not in two  # the 8 M limit stays


def test_round_limit_stays_8m():
    assert CFG["supervisor"]["max_tokens_per_round"] == 8_000_000
