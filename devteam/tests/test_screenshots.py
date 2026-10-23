"""Screenshots for the tester: signed in, per viewport / theme / locale, before shots from dev,
routes on the runs dir, a fresh packet folder each round, the server stopped after."""

import copy
import json
import socket
import sqlite3
from pathlib import Path

import pytest
import yaml
from conftest import CFG, HERE, remote_of, team_for
from langgraph.checkpoint.sqlite import SqliteSaver

from devteam import gates as G
from devteam import procs
from devteam import real as R
from devteam.agents import FakeAgents
from devteam.graph import Team, build_graph

pytestmark = pytest.mark.usefixtures("fast_poll")


class Fast(FakeAgents):
    delay = 0


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
    assert app["shoot"].endswith(" {theme} {lang}") and "{size}" in pipe["shoot"]
    assert G.variants(pipe) == [("1440x900", "", ""), ("390x844", "", "")]
    text = G.SHOOT.read_text()
    assert "colorScheme: theme" in text and "theme = ''" in text


def test_web_tasks_get_before_screenshots_from_dev(repo, tmp_path):
    team, _ = make(repo, developers=web_shots(tmp_path, "touch {png}"))
    team.fetch("dev")
    s = {"task": {"id": "T-002"}, "assignee": "dev_app", "worktree": str(repo)}
    shots, _ = G.dev_screenshots(team, s, ["/", "/viewer/x"])
    assert [Path(p).name for p in shots] == ["dev-root.png", "dev-viewer-x.png"]
    assert not (team.home / "check-wt" / "T-002-dev").exists()  # the dev checkout is removed


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


# --- 2. dev_app screenshots in every locale -------------------------------------------------------
def test_variants_cover_every_locale_and_the_before_shots_only_the_first():
    sc = {"viewports": ["1440x900", "390x844"], "themes": ["light", "dark"],
          "locales": ["ru", "kk", "en"]}  # fmt: skip
    assert G.variants(sc) == [
        ("1440x900", "light", "ru"),
        ("1440x900", "dark", "ru"),
        ("390x844", "light", "ru"),
        ("390x844", "dark", "ru"),
        ("1440x900", "light", "kk"),
        ("390x844", "light", "kk"),
        ("1440x900", "light", "en"),
        ("390x844", "light", "en"),
    ]
    assert G.variants(sc, before=True) == G.variants(sc)[:4]
    assert G.variants({}) == [("1440x900", "", ""), ("390x844", "", "")]  # no locales: as before


def test_dev_app_is_shot_in_ru_kk_and_en(repo, tmp_path):
    args = tmp_path / "args.txt"
    port = procs.free_port()
    devs = copy.deepcopy(CFG["developers"])
    devs["dev_app"]["setup"] = []
    devs["dev_app"]["screenshots"] = {
        "serve": f"exec python3 -m http.server {port} --bind 127.0.0.1",
        "url": f"http://127.0.0.1:{port}",
        "shoot": f"echo {{size}} {{theme}} {{lang}} >> {args}; touch {{png}}",
        "viewports": ["1440x900"],
        "locales": ["ru", "kk", "en"],
    }
    team, _ = make(repo, developers=devs)
    s = {"task": {"id": "T-002"}, "assignee": "dev_app", "worktree": str(repo), "routes": ["/"]}
    shots, problems = G.screenshots(team, s, s["routes"])
    assert problems == [] and G.expected_shots(team.cfg, s) == 3
    assert [Path(p).name for p in shots] == [
        "root-1440x900-ru.png",
        "root-1440x900-kk.png",
        "root-1440x900-en.png",
    ]
    # no theme: still an (empty) argument, so the locale stays shoot.mjs's 6th
    assert args.read_text().split("\n")[:3] == ["1440,900  ru", "1440,900  kk", "1440,900  en"]


def test_config_and_shoot_script_set_the_app_locale():
    cfg = yaml.safe_load((HERE / "config.yaml").read_text())
    app = cfg["developers"]["dev_app"]["screenshots"]
    assert app["locales"] == ["ru", "kk", "en"] and app["shoot"].endswith(" {theme} {lang}")
    assert len(G.variants(app)) == 8  # 4 in ru (light/dark x 2 viewports), 2 each in kk, en
    text = G.SHOOT.read_text()
    assert "localStorage.setItem('qarau.lang', l)" in text and "addInitScript" in text
    i18n = (HERE.parent / "web" / "src" / "i18n" / "index.ts").read_text()
    assert "'qarau.lang'" in i18n  # the key the app really reads


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


def test_screenshots_stop_the_server_and_report_failures(repo, tmp_path):
    port = socket.socket()
    port.bind(("127.0.0.1", 0))
    n = port.getsockname()[1]
    port.close()
    team = team_for(repo)
    team.cfg["developers"]["dev_app"]["screenshots"] = {
        "serve": f"python3 -m http.server {n} --bind 127.0.0.1",
        "url": f"http://127.0.0.1:{n}",
        "shoot": "case {url} in *broken*) exit 1;; esac; echo png > {png}",
        "viewports": ["1440x900"],  # one shot per route
    }
    s = {"task": {"id": "A1a"}, "assignee": "dev_app", "worktree": str(repo)}
    paths, problems = G.screenshots(team, s, ["/", "/viewer/broken"])
    assert [p.rsplit("/", 1)[1] for p in paths] == ["root.png"]
    assert problems and problems[0].startswith("/viewer/broken")
    assert not team._procs  # server stopped and unregistered
    probe = socket.socket()
    assert probe.connect_ex(("127.0.0.1", n)) != 0  # port free for the next round
    probe.close()
