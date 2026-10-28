"""Owner rule: no step leaves a process or a listening port behind."""

import copy
import socket
import subprocess
import sys
import time

import pytest
import yaml
from conftest import CFG, remote_of

from devteam import gates as G
from devteam.agents import FakeAgents
from devteam.graph import Team

LISTEN = """import socket, sys, time
s = socket.socket()
s.bind(("127.0.0.1", int(sys.argv[2]) if len(sys.argv) > 2 else 0))
s.listen()
open(sys.argv[1], "w").write(str(s.getsockname()[1]))
time.sleep(120)
"""
ESCAPE = """import os, subprocess, sys, time
subprocess.Popen([sys.executable, sys.argv[1], sys.argv[2]], start_new_session=True)
while not (os.path.exists(sys.argv[2]) and open(sys.argv[2]).read()):
    time.sleep(0.05)  # exit only once the escaped child listens
"""


@pytest.fixture
def team(repo):
    return Team(repo, copy.deepcopy(CFG), {}, FakeAgents(), fake=True, remote=remote_of(repo))


@pytest.fixture
def scripts(tmp_path):
    (tmp_path / "listen.py").write_text(LISTEN)
    (tmp_path / "escape.py").write_text(ESCAPE)
    return tmp_path


def wait_port(path, secs=10) -> int:
    for _ in range(secs * 20):
        if path.exists() and path.read_text():
            return int(path.read_text())
        time.sleep(0.05)
    raise AssertionError("listener did not start")


def is_open(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def warnings(team) -> list[str]:
    log = team.home / "events.log"
    events = yaml.safe_load(log.read_text()) if log.exists() else []
    return [e["msg"] for e in events or [] if e["msg"].startswith("WARNING")]


def test_a_server_left_in_the_background_is_killed_and_reported(team, scripts):
    pf = scripts / "port"
    r = team.run_proc(
        f"{sys.executable} {scripts / 'listen.py'} {pf} & sleep 1", cwd=str(scripts), timeout=30
    )
    assert r["ok"]
    port = wait_port(pf)
    assert not is_open(port)  # gone when the step ended
    (w,) = warnings(team)
    assert f"left port {port} listening" in w


def test_a_server_that_moved_to_its_own_session_is_found_by_the_marker(team, scripts):
    pf = scripts / "port"
    r = team.run_proc(
        [sys.executable, str(scripts / "escape.py"), str(scripts / "listen.py"), str(pf)],
        cwd=str(scripts),
        timeout=30,
        agent="dev_app",
    )
    assert r["ok"]
    port = wait_port(pf)
    assert not is_open(port)  # not in the step's process group, found by KEREGE_STEP
    (w,) = warnings(team)
    assert w.startswith("WARNING") and f"left port {port} listening" in w


def test_a_timed_out_step_is_killed_with_its_server(team, scripts):
    pf = scripts / "port"
    r = team.run_proc(f"{sys.executable} {scripts / 'listen.py'} {pf}", cwd=str(scripts), timeout=2)
    assert not r["ok"] and "timed out" in r["output"]
    assert not is_open(wait_port(pf))


def test_a_clean_step_has_no_warning(team, scripts):
    assert team.run_proc("echo hi", cwd=str(scripts), timeout=10)["ok"]
    assert warnings(team) == []


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def shots_cfg(team, scripts, port: int):
    team.cfg["developers"]["dev_app"]["screenshots"] = {
        "serve": f"touch {scripts / 'served'} && {sys.executable} -m http.server {port} "
        "--bind 127.0.0.1",
        "url": f"http://127.0.0.1:{port}",
        "shoot": "touch {png}",
        "viewports": ["1440x900"],  # one shot per route
    }


def task(team, scripts):
    return {"task": {"id": "T-002"}, "assignee": "dev_app", "worktree": str(scripts)}


def test_screenshots_refuse_a_stale_server_on_the_port(team, scripts):
    port = free_port()
    stale = subprocess.Popen(  # like the old :5199 left by an earlier session
        [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"], cwd=scripts
    )
    try:
        for _ in range(100):
            if is_open(port):
                break
            time.sleep(0.05)
        shots_cfg(team, scripts, port)
        paths, problems = G.screenshots(team, task(team, scripts), ["/"])
        assert paths == [] and "stale server" in problems[0]
        assert not (scripts / "served").exists()  # ours was never started
    finally:
        stale.kill()
        stale.wait()


def test_screenshots_start_a_fresh_server_and_stop_it(team, scripts):
    port = free_port()
    shots_cfg(team, scripts, port)
    paths, problems = G.screenshots(team, task(team, scripts), ["/", "/viewer/room"])
    assert problems == [] and len(paths) == 2
    assert (scripts / "served").exists()
    assert not is_open(port)  # stopped when the shots were taken


def slept(monkeypatch, secs: float):
    """time.time runs `secs` ahead of the monotonic clock from its second call on: the machine
    slept that long during the first step."""
    real, calls = time.time, []

    def wall():
        calls.append(1)
        return real() + (secs if len(calls) % 2 == 0 else 0)  # (start, end) of each step

    monkeypatch.setattr(time, "time", wall)


def test_a_check_that_failed_while_the_machine_slept_runs_once_more(team, tmp_path, monkeypatch):
    """2026-10-27: a check that ran while the Mac slept came back failed and cost a round."""
    flaky = f"test -f {tmp_path / 'ran'} || {{ touch {tmp_path / 'ran'}; exit 1; }}"
    slept(monkeypatch, 3600)
    assert team.run_proc(flaky, cwd=str(tmp_path), timeout=10)["ok"]  # the second run passes
    log = yaml.safe_load((team.home / "events.log").read_text())
    assert [e["msg"][:40] for e in log] == ["the machine slept 60 min during `test -f"]
    # failing again after the re-run: failed (once more only)
    r = team.run_proc("exit 3", cwd=str(tmp_path), timeout=10)
    assert not r["ok"] and r["code"] == 3
    assert len(yaml.safe_load((team.home / "events.log").read_text())) == 2


@pytest.mark.parametrize("secs", [0, 59])
def test_a_failed_check_without_sleep_is_not_run_again(team, tmp_path, monkeypatch, secs):
    slept(monkeypatch, secs)
    flaky = f"test -f {tmp_path / 'ran'} || {{ touch {tmp_path / 'ran'}; exit 1; }}"
    assert not team.run_proc(flaky, cwd=str(tmp_path), timeout=10)["ok"]
    assert not (team.home / "events.log").exists()


def test_a_watched_developer_run_is_never_run_again(team, tmp_path, monkeypatch):
    slept(monkeypatch, 3600)
    r = team.run_proc("exit 1", cwd=str(tmp_path), timeout=10, watch=lambda *a: "")
    assert not r["ok"] and not (team.home / "events.log").exists()
