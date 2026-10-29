"""Screenshots for the tester: signed in, per viewport / theme / locale, before shots from dev,
routes on the runs dir, a fresh packet folder each round, the server stopped after."""

import copy
import json
import os
import socket
import sqlite3
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml
from conftest import CFG, HERE, remote_of, team_for
from langgraph.checkpoint.sqlite import SqliteSaver
from test_safety import TODO

from devteam import gates as G
from devteam import procs
from devteam import real as R
from devteam.agents import FakeAgents
from devteam.graph import Team, build_graph, validate_plan

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


def test_against_the_seeded_api_the_tester_signs_in_as_its_engineer(repo, tmp_path):
    """UI-evidence-b, 2026-10-28: the .env user matched no seeded user, /api/me answered 401 and
    the shots showed the role error with no decision buttons."""
    seen = tmp_path / "env.txt"
    devs = web_shots(tmp_path, f"env | grep ^QARAU_TEST_ > {seen}; touch {{png}}")
    sc = devs["dev_app"]["screenshots"]
    env_login = {"QARAU_TEST_USER": "tester", "QARAU_TEST_PASSWORD": "a-long-test-password"}
    s = {"task": {"id": "T-002"}, "assignee": "dev_app", "worktree": str(repo)}
    sc["login"] = {"user": "engineer", "token": "seeded-token"}
    team, _ = make(repo, developers=devs, secrets=env_login)
    assert G.screenshots(team, s, ["/"])[1] == []
    assert sorted(seen.read_text().split()) == [
        "QARAU_TEST_PASSWORD=a-long-test-password",
        "QARAU_TEST_USER=tester",
    ]  # no api: .env
    sc["api"] = "exec python3 -m http.server {api_port} --bind 127.0.0.1"
    assert G.screenshots(team, s, ["/"])[1] == []
    assert sorted(seen.read_text().split()) == [
        "QARAU_TEST_PASSWORD=seeded-token",
        "QARAU_TEST_USER=engineer",
    ]
    log = team.home / "events.log"
    assert not log.exists() or "WARNING" not in log.read_text()


def test_config_signs_dev_app_in_as_the_seeded_engineer():
    sc = yaml.safe_load((HERE / "config.yaml").read_text())["developers"]["dev_app"]["screenshots"]
    assert sc["login"]["user"] == "engineer"
    assert f"QARAU_DEV_ENGINEER_TOKEN={sc['login']['token']} " in sc["api"]


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


# --- 4. web pages are shot against the real API on SQLite (U2 saw "could not load sites") -------
def test_dev_app_screenshots_start_the_api_on_a_fresh_sqlite_first_and_stop_it_after(
    repo, tmp_path
):
    seen = tmp_path / "serve.txt"
    devs = web_shots(tmp_path, "touch {png}")
    sc = devs["dev_app"]["screenshots"]
    serve = "exec python3 -m http.server {api_port} --bind 127.0.0.1"
    sc["api"] = f"echo {{db}} > {tmp_path}/db.txt && {serve}"
    sc["serve"] = f"echo {{api_port}} > {seen} && {sc['serve']}"
    team, _ = make(repo, developers=devs)
    db = team.home / "api" / "T-002.sqlite"
    db.parent.mkdir()
    db.write_text("from the last round")
    s = {"task": {"id": "T-002"}, "assignee": "dev_app", "worktree": str(repo)}
    shots, problems = G.screenshots(team, s, ["/"])
    assert problems == [] and len(shots) == 1
    assert (tmp_path / "db.txt").read_text().strip() == str(db) and not db.exists()  # fresh
    api_port = int(seen.read_text())  # the same free port for the API and Vite's proxy
    assert api_port != int(sc["url"].rsplit(":", 1)[1])
    assert not team._procs  # both servers stopped and unregistered
    probe = socket.socket()
    assert probe.connect_ex(("127.0.0.1", api_port)) != 0  # the API port is free again
    probe.close()
    sc["api"] = "exit 3"  # an API that dies is a named problem, never a page shot without it
    shots, problems = G.screenshots(team, s, ["/"])
    assert shots == [] and problems == ["api exited with 3: exit 3"]


def test_config_runs_the_real_api_seeded_on_sqlite_behind_vite_for_dev_app():
    cfg = yaml.safe_load((HERE / "config.yaml").read_text())
    app, pipe = (cfg["developers"][d]["screenshots"] for d in ("dev_app", "dev_pipeline"))
    assert "sqlite:///{db}" in app["api"] and "alembic upgrade head" in app["api"]
    assert "python -m app.seed" in app["api"] and "--port {api_port}" in app["api"]
    assert 'QARAU_API_TOKEN=""' in app["api"]  # open dev mode: any password signs the tester in
    assert "QARAU_API_PROXY=http://127.0.0.1:{api_port}" in app["serve"]
    assert "api" not in pipe  # pipeline tasks show runs through Vite's own /api/runs middleware
    seed = (HERE.parent / "backend" / "app" / "seed.py").read_text()
    for var in ("QARAU_DEV_ADMIN_TOKEN", "QARAU_DEV_ENGINEER_TOKEN", "QARAU_DEV_VIEWER_TOKEN"):
        assert var in app["api"] and var.lower() in seed.lower().replace("_", "_")
    assert "QARAU_API_PROXY" in (HERE.parent / "web" / "vite.config.ts").read_text()


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


# --- 7. a page still loading is shot labelled NOT-READY, never silently (UI-report, 2026-10-28)
def test_a_not_ready_page_is_labelled_in_its_file_name_and_named_for_the_developer(repo, tmp_path):
    seen = tmp_path / "ready.txt"
    shoot = f"echo $QARAU_SHOOT_READY_S > {seen}; touch {{png}}; "
    shoot += "case {url} in *report*) echo 'NOT READY after 7 s: 1 element(s) busy';; esac"
    team, _ = make(repo, developers=web_shots(tmp_path, shoot))
    team.cfg["gates"]["screenshot_ready_timeout_s"] = 7
    s = {"task": {"id": "T-002"}, "assignee": "dev_app", "worktree": str(repo)}
    shots, problems = G.screenshots(team, s, ["/", "/inspections/demo/report"])
    assert [Path(p).name for p in shots] == [
        "root.png",
        "inspections-demo-report-NOT-READY-after-7s.png",
    ]
    assert all(Path(p).exists() for p in shots) and seen.read_text().strip() == "7"
    assert len(problems) == 1
    assert problems[0].startswith("inspections-demo-report-NOT-READY-after-7s.png: page not ready")
    assert 'aria-busy="true"' in problems[0] and "1 element(s) busy" in problems[0]


WEB = HERE.parent / "web"
BUSY_PAGE = '<html><body><main aria-busy="true">Отчёт формируется…</main>{script}</body></html>'


@pytest.mark.skipif(
    not (WEB / "node_modules" / "@playwright" / "test").exists(),
    reason="needs web/node_modules (pnpm install) and Playwright's Chromium",
)
@pytest.mark.parametrize(
    ("script", "ready"),
    [
        (
            "<script>setTimeout(() => document.querySelector('main').ariaBusy = 'false', 2000)"
            "</script>",
            True,
        ),
        ("", False),
    ],
    ids=["clears-after-2s", "never-clears"],
)
def test_shoot_waits_for_the_page_to_clear_aria_busy(tmp_path, script, ready):
    page, png = tmp_path / "page.html", tmp_path / "page.png"
    page.write_text(BUSY_PAGE.format(script=script))
    env = {k: v for k, v in os.environ.items() if not k.startswith("QARAU_TEST_")}
    r = subprocess.run(
        ["node", str(G.SHOOT), page.as_uri(), str(png), "800,600", "0"],
        cwd=WEB,
        env={**env, "QARAU_SHOOT_READY_S": "6"},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert r.returncode == 0, r.stderr
    assert png.exists()
    out = G.NOT_READY.search(r.stdout)
    if ready:
        assert out is None, r.stdout
    else:
        assert out and out[1] == "6" and 'aria-busy="true"' in out[2]


# --- 8. guest routes: shot as an event guest (DEMO1-ui, 2026-10-29) -------------------------------
GUEST = {"path": "/viewer/notre-dame-p3e", "session": "guest"}


def guest_shots(tmp_path, shoot="touch {png}", create="echo 'code: ABCD2345'"):
    """dev_app against a stand-in API, with screenshots.guest whose commands log their calls."""
    log = tmp_path / "calls.txt"
    devs = web_shots(tmp_path, f"echo shoot {{url}} $QARAU_TEST_EVENT_CODE "
                     f"user=$QARAU_TEST_USER >> {log}; {shoot}")  # fmt: skip
    sc = devs["dev_app"]["screenshots"]
    sc["api"] = "exec python3 -m http.server {api_port} --bind 127.0.0.1"
    sc["login"] = {"user": "engineer", "token": "seeded-token"}
    sc["guest"] = {
        "run": "notre-dame-p3e",
        "create": f"echo create {{db}} {{name}} {{run}} {{expires}} >> {log}; {create}",
        "purge": f"echo purge {{db}} {{name}} >> {log}",
    }
    return devs, log


def test_a_guest_route_is_shot_signed_in_with_a_short_lived_event_code(repo, tmp_path):
    devs, log = guest_shots(tmp_path)
    team, _ = make(repo, developers=devs)
    s = {"task": {"id": "T-002"}, "assignee": "dev_app", "worktree": str(repo)}
    shots, problems = G.screenshots(team, s, ["/?signed-out", GUEST])
    assert problems == [] and [Path(p).name for p in shots] == [
        "signed-out.png",
        "viewer-notre-dame-p3e.png",
    ]
    create, engineer, guest, purge = log.read_text().splitlines()
    _, db, name, run, expires = create.split()
    assert db == str(team.home / "api" / "T-002.sqlite")  # the test database the API uses
    assert (name, run) == ("shoot-notre-dame-p3e", "notre-dame-p3e")
    left = datetime.fromisoformat(expires) - datetime.now(UTC)
    assert timedelta(minutes=55) < left <= timedelta(hours=1)
    assert engineer.endswith("/?signed-out user=engineer")  # plain routes: the seeded engineer
    assert guest.endswith("/viewer/notre-dame-p3e ABCD2345 user=")  # the code, no user login
    assert purge == f"purge {db} {name}"


def test_the_guest_event_is_purged_also_when_a_shot_fails(repo, tmp_path):
    devs, log = guest_shots(tmp_path, shoot="exit 1")
    team, _ = make(repo, developers=devs)
    s = {"task": {"id": "T-002"}, "assignee": "dev_app", "worktree": str(repo)}
    shots, problems = G.screenshots(team, s, [GUEST])
    assert shots == [] and len(problems) == 1
    assert log.read_text().splitlines()[-1].startswith("purge ")


def test_no_event_no_guest_shot_and_nothing_to_purge(repo, tmp_path):
    devs, log = guest_shots(tmp_path, create="echo 'unknown run id: notre-dame-p3e'; exit 2")
    team, _ = make(repo, developers=devs)
    s = {"task": {"id": "T-002"}, "assignee": "dev_app", "worktree": str(repo)}
    shots, problems = G.screenshots(team, s, ["/", GUEST])
    assert [Path(p).name for p in shots] == ["root.png"]  # the guest shot is missing: round fails
    assert len(problems) == 1 and "unknown run id" in problems[0]
    assert not any(x.startswith("purge") for x in log.read_text().splitlines())


def test_dev_before_shots_leave_out_guest_routes(repo, tmp_path):
    devs, log = guest_shots(tmp_path)
    team, _ = make(repo, developers=devs)
    s = {"task": {"id": "T-002"}, "assignee": "dev_app", "worktree": str(repo)}
    shots, problems = G.screenshots(team, s, ["/", GUEST], prefix="dev-", before=True)
    assert [Path(p).name for p in shots] == ["dev-root.png"] and problems == []
    assert "create" not in log.read_text()


def test_without_screenshots_guest_a_guest_route_is_shot_as_the_engineer(repo, tmp_path):
    devs, log = guest_shots(tmp_path)
    del devs["dev_app"]["screenshots"]["guest"]
    team, _ = make(repo, developers=devs)
    s = {"task": {"id": "T-002"}, "assignee": "dev_app", "worktree": str(repo)}
    shots, problems = G.screenshots(team, s, [GUEST])
    assert problems == [] and [Path(p).name for p in shots] == ["viewer-notre-dame-p3e.png"]
    assert log.read_text().split() == ["shoot", f"{devs['dev_app']['screenshots']['url']}"
                                       "/viewer/notre-dame-p3e", "user=engineer"]  # fmt: skip


def guest_plan(*routes):
    crit = ["a guest's viewer shows the demo banner"]
    return [{"task_id": "T-002", "assignee": "dev_app", "plan": "p", "criteria": crit,
             "routes": list(routes)}]  # fmt: skip


@pytest.mark.parametrize(
    ("routes", "why"),
    [
        (["/?signed-out", "/viewer/notre-dame-p3e"], "no route has session: guest"),
        ([{"path": "/viewer/notre-dame", "session": "guest"}], "only the shared run"),
        (["/?signed-out", GUEST], None),
    ],
    ids=["no-guest-route", "unshared-run", "ok"],
)
def test_a_task_tagged_guest_plans_its_guest_screens_as_guest_routes(routes, why):
    todo = [{**t, "tags": ["guest"]} if t["id"] == "T-002" else t for t in TODO]
    if why:
        with pytest.raises(ValueError, match=why):
            validate_plan(guest_plan(*routes), todo, CFG)
    else:
        (a,) = validate_plan(guest_plan(*routes), todo, CFG)
        assert a["routes"] == routes
    off = copy.deepcopy(CFG)  # no screenshots.guest: plans are checked as before
    del off["developers"]["dev_app"]["screenshots"]["guest"]
    validate_plan(guest_plan("/?signed-out", "/viewer/notre-dame-p3e"), todo, off)


# The sign-in page's event-code form as DEMO1-ui builds it: a toggle opens #login-event-code; the
# form goes on success, an alert says a refused code.
CODE_PAGE = """<html><body><form id="f">
<button type="button" aria-expanded="false" onclick="document.getElementById('c').hidden = false">
I have an event code</button>
<div id="c" hidden><input id="login-event-code"></div><button type="submit">Sign in</button></form>
<script>document.getElementById('f').onsubmit = (e) => { e.preventDefault();
  if (document.getElementById('login-event-code').value === 'ABCD2345') e.target.remove();
  else document.body.insertAdjacentHTML('beforeend', '<p role="alert">expired</p>'); };</script>
</body></html>"""


@pytest.mark.skipif(
    not (WEB / "node_modules" / "@playwright" / "test").exists(),
    reason="needs web/node_modules (pnpm install) and Playwright's Chromium",
)
@pytest.mark.parametrize(("code", "ok"), [("ABCD2345", True), ("WRONG000", False)])
def test_shoot_signs_a_guest_in_with_the_event_code_form(tmp_path, code, ok):
    (tmp_path / "index.html").write_text(CODE_PAGE)
    (tmp_path / "page.html").write_text("<html><body>viewer</body></html>")
    port = procs.free_port()
    server = subprocess.Popen(
        ["python3", "-m", "http.server", str(port), "--bind", "127.0.0.1"],
        cwd=tmp_path, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )  # fmt: skip
    try:
        for _ in range(50):  # until the server answers
            if G._answers(f"http://127.0.0.1:{port}/"):
                break
            time.sleep(0.2)
        env = {k: v for k, v in os.environ.items() if not k.startswith("QARAU_TEST_")}
        png = tmp_path / "page.png"
        r = subprocess.run(
            ["node", str(G.SHOOT), f"http://127.0.0.1:{port}/page.html", str(png), "800,600", "0"],
            cwd=WEB, env={**env, "QARAU_TEST_EVENT_CODE": code, "QARAU_SHOOT_READY_S": "2"},
            capture_output=True, text=True, timeout=60,
        )  # fmt: skip
    finally:
        server.kill()
        server.wait()
    assert (r.returncode == 0) == ok, r.stderr[-500:]
    assert png.exists() == ok
    assert ok or "sign-in failed: the event code was refused" in r.stderr
    assert code not in r.stdout + r.stderr  # the code is never printed


def test_the_brain_may_answer_guest_routes_only_with_screenshots_guest(repo, monkeypatch):
    seen = []
    monkeypatch.setattr(R, "llm_call", lambda team, role, task, system, prompt, schema, **kw: (
        seen.append(schema["properties"]["assignments"]["items"]["properties"]["routes"])
        or {"assignments": [], "needs_split": []}))  # fmt: skip
    team = team_for(repo)
    R.RealAgents().plan(team, [])
    del team.cfg["developers"]["dev_app"]["screenshots"]["guest"]
    R.RealAgents().plan(team, [])
    on, off = seen
    guest = {"type": "object", "properties": {"path": {"type": "string"},
             "session": {"type": "string", "enum": ["guest"]}},
             "required": ["path", "session"], "additionalProperties": False}  # fmt: skip
    assert on["items"]["anyOf"] == [{"type": "string"}, guest]
    assert off["items"] == {"type": "string"}  # as before
