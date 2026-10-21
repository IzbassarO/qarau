"""Owner, 2026-10-19, after the P3e rehearsal (cont-20261019-080942): screenshots behind the
login, routes that show the task's own output, loop detection by criterion number, UX1 and
the frontend-design skill."""

import copy
import sqlite3
from pathlib import Path

import pytest
import yaml
from conftest import CFG, HERE, real_backlog, remote_of
from langgraph.checkpoint.sqlite import SqliteSaver

from devteam import gates as G
from devteam import graph as GR
from devteam import procs
from devteam import real as R
from devteam import scope as SC
from devteam.agents import FakeAgents
from devteam.graph import PlanError, Team, build_graph, criterion_key, validate_plan

ROOT = HERE.parent


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


def web_shots(tmp_path, shoot):
    port = procs.free_port()
    devs = copy.deepcopy(CFG["developers"])
    devs["dev_app"]["setup"] = []
    devs["dev_app"]["screenshots"] = {
        "serve": f"exec python3 -m http.server {port} --bind 127.0.0.1",
        "url": f"http://127.0.0.1:{port}",
        "shoot": shoot,
        "viewports": ["1440x900"],  # one shot per route
    }
    return devs


# --- 1. the tester signs in before screenshots --------------------------------------------------
def test_screenshots_get_the_test_login_from_env_never_on_the_command_line(repo, tmp_path):
    seen = tmp_path / "env.txt"
    devs = web_shots(
        tmp_path, f"env | grep ^QARAU_TEST_ > {seen}; echo {{shoot}} >> {seen}; touch {{png}}"
    )
    secret = "a-long-test-password"
    team, _ = make(
        repo, developers=devs, secrets={"QARAU_TEST_USER": "tester", "QARAU_TEST_PASSWORD": secret}
    )
    s = {"task": {"id": "T-002"}, "assignee": "dev_app", "worktree": str(repo)}
    shots, problems = G.screenshots(team, s, ["/"])
    assert len(shots) == 1 and problems == []
    env = seen.read_text()
    assert f"QARAU_TEST_PASSWORD={secret}" in env and "QARAU_TEST_USER=tester" in env
    assert str(G.SHOOT) in env and G.SHOOT.exists()


def test_missing_test_login_is_said_not_silent(repo, tmp_path):
    team, _ = make(repo, developers=web_shots(tmp_path, "touch {png}"))
    s = {"task": {"id": "T-002"}, "assignee": "dev_app", "worktree": str(repo)}
    shots, problems = G.screenshots(team, s, ["/"])
    assert len(shots) == 1 and problems == []
    assert (
        "WARNING: no QARAU_TEST_USER / QARAU_TEST_PASSWORD in devteam/.env: pages behind the "
        "login show the login form" in (team.home / "events.log").read_text()
    )


def test_config_shoots_through_the_sign_in_script():
    cfg = yaml.safe_load((HERE / "config.yaml").read_text())
    for dev in ("dev_pipeline", "dev_app"):
        assert cfg["developers"][dev]["screenshots"]["shoot"].startswith("cd web && node {shoot} ")
    text = G.SHOOT.read_text()
    assert "QARAU_TEST_PASSWORD" in text and "console.log" not in text  # never printed
    assert "signed-out" in text  # the login page itself stays reachable


def test_each_route_is_shot_per_viewport_and_theme(repo, tmp_path):
    """Owner, 2026-10-21: dev_app pages in light and dark, at 1440x900 and 390x844."""
    args = tmp_path / "args.txt"
    devs = web_shots(tmp_path, f"echo {{size}} {{theme}} >> {args}; touch {{png}}")
    del devs["dev_app"]["screenshots"]["viewports"]  # the default
    devs["dev_app"]["screenshots"]["themes"] = ["light", "dark"]
    team, _ = make(repo, developers=devs)
    s = {"task": {"id": "T-002"}, "assignee": "dev_app", "worktree": str(repo), "routes": ["/"]}
    shots, problems = G.screenshots(team, s, s["routes"])
    assert problems == [] and G.expected_shots(team.cfg, s) == 4
    assert [Path(p).name for p in shots] == [
        "root-1440x900-light.png",
        "root-1440x900-dark.png",
        "root-390x844-light.png",
        "root-390x844-dark.png",
    ]
    assert args.read_text().split("\n")[:4] == [
        "1440,900 light",
        "1440,900 dark",
        "390,844 light",
        "390,844 dark",
    ]


def test_config_shoots_dev_app_in_both_themes_at_the_default_viewports():
    cfg = yaml.safe_load((HERE / "config.yaml").read_text())
    app, pipe = (cfg["developers"][d]["screenshots"] for d in ("dev_app", "dev_pipeline"))
    assert app["themes"] == ["light", "dark"] and "{size}" in app["shoot"]
    assert app["shoot"].endswith(" {theme}") and "{size}" in pipe["shoot"]
    assert G.variants(pipe) == [("1440x900", ""), ("390x844", "")]
    text = G.SHOOT.read_text()
    assert "colorScheme: theme" in text and "theme = ''" in text


def test_web_tasks_get_before_screenshots_from_dev(repo, tmp_path):
    team, _ = make(repo, developers=web_shots(tmp_path, "touch {png}"))
    team.fetch("dev")
    s = {"task": {"id": "T-002"}, "assignee": "dev_app", "worktree": str(repo)}
    shots, _ = G.dev_screenshots(team, s, ["/", "/viewer/x"])
    assert [Path(p).name for p in shots] == ["dev-root.png", "dev-viewer-x.png"]
    assert not (team.home / "check-wt" / "T-002-dev").exists()  # the dev checkout is removed


# --- 2. routes show the task's own output ---------------------------------------------------
TODO = [
    {"id": "T-001", "area": "core", "writable": ["data/runs"], "runs_before_task": ["room"]},
    {"id": "T-003", "area": "core"},
    {"id": "T-002", "area": "app"},
]


def plan(tid, dev, routes):
    return {"task_id": tid, "assignee": dev, "plan": "p", "criteria": ["c"], "routes": routes}


def test_a_task_that_writes_runs_must_route_to_runs_it_makes():
    with pytest.raises(PlanError, match=r"/viewer/room shows run 'room', which was in data/runs"):
        validate_plan([plan("T-001", "dev_pipeline", ["/", "/viewer/room"])], TODO, CFG)
    (a,) = validate_plan(
        [plan("T-001", "dev_pipeline", ["/", "/viewer/room-2026-10-20"])], TODO, CFG
    )
    assert a["routes"] == ["/", "/viewer/room-2026-10-20"]


def test_a_pipeline_task_routes_only_to_its_quick_benchmark_videos():
    quick = CFG["gates"]["benchmark"]["quick_only"]
    with pytest.raises(PlanError, match="serve its quick benchmark output"):
        validate_plan([plan("T-003", "dev_pipeline", ["/viewer/aegviidu"])], TODO, CFG)
    validate_plan([plan("T-003", "dev_pipeline", [f"/viewer/{quick[0]}"])], TODO, CFG)
    validate_plan([plan("T-002", "dev_app", ["/viewer/anything"])], TODO, CFG)  # runs_dir as is


def test_runs_before_task_come_from_the_kept_start_snapshot(repo):
    team, _ = make(repo)
    for run in ("room", "old-run"):
        (repo / "data" / "runs" / run / "web").mkdir(parents=True)
        (repo / "data" / "runs" / run / "web" / "manifest.json").write_text("{}")
    task = {"id": "T-001", "writable": ["data/runs"]}
    assert team.runs_before_task(task) == ["old-run", "room"]
    (repo / "data" / "runs" / "room-2026-10-20" / "web").mkdir(parents=True)
    (repo / "data" / "runs" / "room-2026-10-20" / "web" / "manifest.json").write_text("{}")
    assert team.runs_before_task(task) == ["old-run", "room"]  # made by the task: not "before"


def test_a_blocked_task_keeps_its_start_snapshot(repo):
    team, graph = make(repo, max_fix_loops=0)
    data = yaml.safe_load(team.backlog_path.read_text())
    data["tasks"][0]["writable"] = ["data/runs"]
    team.backlog_path.write_text(yaml.safe_dump(data, sort_keys=False))
    (repo / "data" / "runs").mkdir(parents=True)
    graph.invoke({}, {"configurable": {"thread_id": "r"}})
    assert {t["id"]: t["status"] for t in team.load_backlog()}["T-001"] == "blocked"
    assert (team.home / "data-snap" / "T-001.json").exists()


# --- 3. "the same reason" = the same criterion number -----------------------------------------
def test_criterion_key_is_the_number_not_the_wording():
    crit = ["first", "/ lists the new runs"]
    a = {"number": 2, "criterion": "The root page lists the runs", "met": False}
    b = {"number": 2, "criterion": "Run list on / shows the dated runs", "met": False}
    assert criterion_key(a, crit) == criterion_key(b, crit) == "criterion #2"
    assert criterion_key({"number": 9, "criterion": "x"}, crit) == "criterion: x"  # out of range


class RewordingTester(Fast):
    """Fails criterion 1 every round, worded differently each time (P3e, 2026-10-19)."""

    rounds = 0

    def judge(self, team, s, checks):
        RewordingTester.rounds += 1
        crit = [
            {
                "number": 1,
                "criterion": f"wording {RewordingTester.rounds}",
                "met": False,
                "note": "",
            }
        ]
        return {"passed": False, "feedback": f"fix it ({RewordingTester.rounds})",
                "criteria": crit, "summary": "", "how_to_try": ""}  # fmt: skip


def test_the_same_criterion_twice_blocks_whatever_the_wording(repo):
    team, graph = make(repo, RewordingTester(), max_fix_loops=3)
    graph.invoke({}, {"configurable": {"thread_id": "r"}})
    t = {t["id"]: t for t in team.load_backlog()}["T-001"]
    assert t["status"] == "blocked" and "twice in a row" in t["blocked_reason"]


def test_the_judge_numbers_its_criteria():
    assert "number" in R.TESTER_SYSTEM and "`number`" in R.TESTER_SYSTEM


# --- 5. UX1: design pass, split to fit the 8 M round limit ---------------------------------------
@pytest.mark.backlog
def test_ux1_is_the_first_dev_app_task_and_split_in_two():
    tasks = real_backlog()
    by = {t["id"]: t for t in tasks}
    a, b = by["UX1a"], by["UX1b"]
    assert a["area"] == b["area"] == "app" and not a.get("depends_on")
    assert b["depends_on"] == ["UX1a"]
    app = [t["id"] for t in tasks if t["area"] == "app"]
    assert app.index("UX1a") == 0 and app.index("UX1b") == 1
    for t in (a, b):
        assert "web/ only" in t["goal"] and "no new features" in t["goal"]
        assert "en/ru/kk" in t["goal"] and "before (dev-*)" in t["goal"]
        assert "supervisor" not in t  # the 8 M limit stays
    assert "login page" in a["goal"] and "control centre" in a["goal"]
    assert "newest first" in b["goal"] and "viewer chrome" in b["goal"]


# --- 7. the frontend-design skill: pinned, licensed, protected, seen by Codex -------------------
def test_frontend_design_skill_is_pinned_licensed_and_visible_to_codex():
    skill = ROOT / ".claude" / "skills" / "frontend-design"
    assert (skill / "SKILL.md").read_text().startswith("---\nname: frontend-design\n")
    assert "Apache License" in (skill / "LICENSE.txt").read_text()
    link = ROOT / ".agents" / "skills" / "frontend-design"  # where codex-cli reads skills
    assert link.is_symlink() and link.resolve() == skill.resolve()
    readme = (ROOT / ".claude" / "skills" / "README.md").read_text()
    assert "dbd4588f9e1033efb41dad4bef2f7947c8993d44" in readme and "Apache-2.0" in readme
    assert "DESIGN.md" in (ROOT / "AGENTS.md").read_text()
    assert (ROOT / "docs" / "design" / "DESIGN.md").exists()


def test_skills_are_protected_and_never_downloaded_by_agents():
    for path in (".claude/skills/frontend-design/SKILL.md", ".agents/skills/x/SKILL.md"):
        assert SC.is_protected(path, CFG)
    assert "DESIGN.md" in R.design_rule(CFG["developers"]["dev_app"])
    assert R.design_rule(CFG["developers"]["dev_pipeline"]) == ""


def test_each_round_starts_with_an_empty_packet_screenshot_folder(repo):
    seen = []

    class Look(Fast):
        def develop(self, team, s):
            folder = team.home / "packets" / s["task"]["id"]
            if s["task"]["id"] == "T-001":
                seen.append(sorted(p.name for p in folder.glob("*")))
            folder.mkdir(parents=True, exist_ok=True)
            (folder / f"root-round-{s.get('rounds', 0) + 1}.png").write_text("png")
            return super().develop(team, s)

    team, graph = make(repo, Look())
    stale = team.home / "packets" / "T-001"
    stale.mkdir(parents=True)
    (stale / "root.png").write_text("from an earlier run")
    graph.invoke({}, {"configurable": {"thread_id": "r"}})
    assert seen == [[], []]  # the fake tester sends round 1 back: two rounds
    assert (team.home / "packets" / "T-001.md").exists()  # the packet itself stays
