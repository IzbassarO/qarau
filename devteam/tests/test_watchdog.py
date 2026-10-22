"""Watchdog (owner, 2026-10-22): a crash of the orchestrator itself is recorded, notified and
resumed once; a second one stops with "needs you"; clean stops are never resumed. Rehearse."""

import copy
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from conftest import CFG, remote_of
from test_continuous import Fast, make

from devteam import continuous as C
from devteam import procs
from devteam import watchdog as W
from devteam.graph import Team

DRIVER = [sys.executable, str(Path(__file__).with_name("kerege_driver.py"))]


@pytest.fixture
def team(repo, monkeypatch):
    monkeypatch.setenv("KEREGE_REPO", str(repo))
    return Team(repo, copy.deepcopy(CFG), {}, Fast(), fake=True, remote=remote_of(repo))


def crash(monkeypatch, kind: str, times: int = 1):
    monkeypatch.setenv("KEREGE_CRASH", kind)
    monkeypatch.setenv("KEREGE_CRASHES", str(times))


def statuses(team):
    return {t["id"]: t["status"] for t in team.load_backlog()}


def runs(team) -> int:
    f = team.home.parents[3] / "crashes"
    return int(f.read_text()) if f.exists() else 0


def test_orchestrator_crash_is_recorded_notified_and_resumed_once(team, monkeypatch, banners):
    crash(monkeypatch, "raise")
    code = W.supervise(team, ["run", "--fake"], cmd=DRIVER, wait_s=0)
    assert code == W.FINISHED
    assert statuses(team) == {"T-001": "done", "T-002": "done", "T-003": "todo"}
    info = json.loads((team.home / "last_crash.json").read_text())
    assert info["error"] == "RuntimeError: injected orchestrator bug"
    assert info["where"].startswith("kerege_driver.py:")  # the line that raised, for the owner
    assert "Traceback" in info["traceback"]
    assert (info["attempt"], info["exit_code"], info["command"]) == (1, 1, "devteam run --fake")
    lines = banners.read_text().splitlines()
    assert lines[0] == (
        f"crashed: RuntimeError: injected orchestrator bug at {info['where']}. "
        "Resuming in 0 s. | Kerege (fake)"
    )
    assert lines[-1] == "run finished: 2 done (T-001, T-002), 0 blocked, API $0.00 | Kerege (fake)"
    log = (team.home / "notifications.log").read_text()
    assert "email skipped: DEVTEAM_NOTIFY_EMAIL not set in devteam/.env" in log
    assert not (team.home / "watchdog.pid").exists() and not (team.home / "run.pid").exists()


def test_a_kill_is_a_crash_too(team, monkeypatch, banners):
    crash(monkeypatch, "kill")
    assert W.supervise(team, ["run", "--fake"], cmd=DRIVER, wait_s=0) == W.FINISHED
    info = json.loads((team.home / "last_crash.json").read_text())
    assert info["error"] == "the orchestrator killed by SIGKILL" and info["exit_code"] == -9
    assert banners.read_text().startswith("crashed: the orchestrator killed by SIGKILL. Resuming")
    assert statuses(team)["T-001"] == "done"


def test_second_crash_stops_and_needs_you(team, monkeypatch, banners):
    crash(monkeypatch, "raise", times=5)
    assert W.supervise(team, ["run", "--fake"], cmd=DRIVER, wait_s=0) == 1
    assert runs(team) == 2  # the run and one resume, never more
    info = json.loads((team.home / "last_crash.json").read_text())
    assert (info["attempt"], info["command"]) == (2, "devteam resume --fake")
    last = banners.read_text().splitlines()[-1]
    assert last.startswith("needs you: RuntimeError: injected orchestrator bug at kerege_driver")
    assert "fix the cause, then `devteam resume`" in last


@pytest.mark.parametrize(
    "kind, code, note",
    [
        ("budget", W.HALTED, "stopped: daily API budget reached"),
        ("limit", W.HALTED, "stopped: usage limit reached"),
        ("stop", W.STOPPED, None),  # the owner stopped it: they know
    ],
)
def test_clean_stops_are_not_resumed(team, monkeypatch, banners, kind, code, note):
    crash(monkeypatch, kind)
    assert W.supervise(team, ["run", "--fake"], cmd=DRIVER, wait_s=0) == code
    assert runs(team) == 1 and not (team.home / "last_crash.json").exists()
    text = banners.read_text() if banners.exists() else ""
    assert "crashed" not in text and "needs you" not in text
    if note:
        assert text == f"run finished: 0 done, 0 blocked, API $0.00; {note} | Kerege (fake)\n"


def test_a_refused_start_is_not_a_crash(team, banners):
    assert W.supervise(team, ["resume", "--fake"], cmd=DRIVER, wait_s=0) == W.REFUSED
    assert not (team.home / "last_crash.json").exists() and not banners.exists()


def test_stop_cancels_the_pending_resume(team, monkeypatch, banners, capsys):
    crash(monkeypatch, "raise")

    def stop_during_the_wait():  # `devteam stop` signals the watchdog too
        while "Resuming" not in (banners.read_text() if banners.exists() else ""):
            time.sleep(0.05)
        os.kill(os.getpid(), signal.SIGTERM)

    threading.Thread(target=stop_during_the_wait, daemon=True).start()
    assert W.supervise(team, ["run", "--fake"], cmd=DRIVER, wait_s=60) == 1
    assert "watchdog: resume cancelled" in capsys.readouterr().out
    assert runs(team) == 1 and statuses(team)["T-001"] != "done"


def test_retry_args():
    assert W.retry_args(["run", "--fake", "--only", "P1"]) == ["resume", "--fake", "--only", "P1"]
    assert W.retry_args(["resume"]) == ["resume"]
    assert W.retry_args(["run", "--continuous"]) == ["run", "--continuous"]  # resumes its rounds


def test_steps_a_killed_orchestrator_left_are_reaped(team):
    marker = procs.new_marker()
    p = subprocess.Popen(["sleep", "300"], start_new_session=True,
                         env={**os.environ, procs.MARKER: marker})  # fmt: skip
    (team.home / "steps").mkdir()
    (team.home / "steps" / f"{p.pid}-{marker}").touch()
    W.reap_steps(team)
    assert p.wait(timeout=5) == -signal.SIGTERM
    assert not list((team.home / "steps").iterdir())


def test_a_developer_loop_crash_stops_the_run_as_a_crash(repo, monkeypatch):
    team, graph = make(repo)

    def broken(self, dev):
        raise KeyError("loop bug")

    monkeypatch.setattr(C.Continuous, "dev_loop", broken)
    cont = C.Continuous(team, graph)
    s = cont.run()
    assert isinstance(cont.crash, KeyError)  # cli raises it: recorded, resumed by the watchdog
    assert "the developer loop crashed: 'loop bug'" in s["stop_reason"]


def test_blocked_task_notifies_but_an_owner_stop_does_not(team, banners):
    team.update_task("T-001", status="blocked", blocked_reason="tester: 3 send-backs")
    team.update_task("T-001", status="blocked", blocked_reason="tester: 3 send-backs")  # no news
    team.update_task("T-002", status="blocked", blocked_reason="stopped by the owner (A7)")
    assert banners.read_text() == "T-001 blocked: tester: 3 send-backs | Kerege (fake)\n"


def test_heartbeat_is_written_and_aged(team):
    W.heartbeat(team)
    for _ in range(50):
        if (team.home / "heartbeat").exists():
            break
        time.sleep(0.05)
    age = W.heartbeat_age(team, time.time() + 400)
    assert age is not None and 399 <= age <= 402


def test_rehearse_runs_the_first_todo_task_of_each_developer(repo, monkeypatch):
    monkeypatch.setenv("KEREGE_REPO", str(repo))
    out = subprocess.run([*DRIVER, "rehearse"], capture_output=True, text=True, timeout=600)
    assert out.returncode == 0, out.stdout[-2000:] + out.stderr[-2000:]
    assert "`devteam run --fake --only T-001,T-002` on a fresh fake state" in out.stdout
    assert "rehearse: PASS" in out.stdout and "T-001 done, T-002 done" in out.stdout


def test_rehearse_fails_on_a_crash_even_when_the_resume_recovered(repo, monkeypatch):
    monkeypatch.setenv("KEREGE_REPO", str(repo))
    crash(monkeypatch, "raise")
    out = subprocess.run([*DRIVER, "rehearse"], capture_output=True, text=True, timeout=600)
    assert out.returncode == 1
    assert "rehearse: FAIL" in out.stdout
    assert "crashed, see .state/fake/last_crash.json" in out.stdout


def test_dashboard_shows_heartbeat_age_red_when_stale_during_a_run(team):
    from test_dashboard import fake_gh

    from devteam import dashboard as D

    now = time.time()
    (team.home / "run.pid").write_text(str(os.getpid()))  # a live run
    (team.home / "heartbeat").write_text(f"{now - 20:.0f} 1\n")
    d = D.collect(team, fake_gh([], []), now=now)
    assert d["heartbeat"] == pytest.approx(20, abs=1) and not d["hb_stale"]
    assert "last heartbeat 20 s ago</div>" in D.render(d)
    (team.home / "heartbeat").write_text(f"{now - 600:.0f} 1\n")
    d = D.collect(team, fake_gh([], []), now=now)
    assert d["hb_stale"] and "heartbeat" in [a["kind"] for a in d["attention"]]
    assert "class='s bad'>last heartbeat 10 min ago — no sign of life" in D.render(d)
    (team.home / "run.pid").unlink()  # stopped: an old heartbeat is only history
    assert not D.collect(team, fake_gh([], []), now=now)["hb_stale"]


def test_dashboard_lists_a_crash_until_a_newer_heartbeat(team):
    from test_dashboard import fake_gh

    from devteam import dashboard as D

    now = time.time()
    (team.home / "heartbeat").write_text(f"{now - 100:.0f} 1\n")
    W._write(team, {"error": "RuntimeError: boom", "where": "graph.py:1", "attempt": 2})
    kinds = [a["kind"] for a in D.collect(team, fake_gh([], []), now=now)["attention"]]
    assert "crash" in kinds
    (team.home / "heartbeat").write_text(f"{now + 5:.0f} 1\n")  # a run beat since: resolved
    kinds = [a["kind"] for a in D.collect(team, fake_gh([], []), now=now + 5)["attention"]]
    assert "crash" not in kinds
