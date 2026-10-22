"""v2: Codex developer (dev_app), quick benchmark policy, benchmark cache per pipeline tree,
per-video tolerances, screenshots for the tester, subscription load in the daily report."""

import copy
import json
import os
import socket
import sqlite3
import threading

from conftest import CFG, git, remote_of
from langgraph.checkpoint.sqlite import SqliteSaver
from test_gates import bench_repo

from devteam import costs
from devteam import gates as G
from devteam.agents import FakeAgents
from devteam.graph import Team, build_graph
from devteam.real import RealAgents, parse_codex, quick_benchmark
from devteam.report import write_report

# Real `codex exec --json` output (codex-cli 0.162.1), trimmed.
CODEX_OK = """{"type":"thread.started","thread_id":"01a13f6c-eace"}
{"type":"turn.started"}
{"type":"item.completed","item":{"id":"item_0","type":"agent_message","text":"working"}}
{"type":"item.completed","item":{"id":"item_1","type":"command_execution","command":"ls"}}
{"type":"item.completed","item":{"id":"item_2","type":"agent_message","text":"DONE"}}
{"type":"turn.completed","usage":{"input_tokens":29513,"cached_input_tokens":26752,"output_tokens":58}}
"""
CODEX_FAILED = """{"type":"thread.started","thread_id":"01a13f6d"}
{"type":"turn.started"}
{"type":"error","message":"status 400: model not supported"}
{"type":"turn.failed","error":{"message":"usage limit reached"}}
"""


def team_for(repo, **cfg_over):
    cfg = copy.deepcopy(CFG)
    cfg.update(cfg_over)
    return Team(repo, cfg, {}, FakeAgents(), fake=True, remote=remote_of(repo))


def test_parse_codex():
    ok = parse_codex(CODEX_OK, True, "", "")
    assert ok == {
        "session": "01a13f6c-eace",
        "summary": "DONE",
        "error": False,
        "usage": {"in": 29513, "out": 58, "usd": 0.0},
    }
    bad = parse_codex(CODEX_FAILED, False, "", "")
    assert bad["error"] and bad["summary"] == "usage limit reached"
    crashed = parse_codex("", False, "boom", "old")
    assert crashed["error"] and crashed["summary"] == "boom" and crashed["session"] == "old"


def test_codex_developer_runs_resumes_and_logs_load(repo, tmp_path, monkeypatch):
    """A stand-in `codex` on PATH: records its argv and stdin, answers like the real one."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "codex").write_text(
        "#!/bin/sh\n"
        f'echo "$@" >> {tmp_path}/argv\n'
        f"cat > {tmp_path}/stdin\n"
        f'[ -n "$OPENAI_API_KEY" ] && echo leaked > {tmp_path}/key\n'
        f"cat <<'EOF'\n{CODEX_OK}EOF\n"
    )
    (bindir / "codex").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-pass")
    team = team_for(repo)
    s = {
        "task": {"id": "A1a", "title": "Shell", "goal": "g"},
        "assignee": "dev_app",
        "plan": "p",
        "criteria": ["c"],
        "worktree": str(repo),
    }
    out = RealAgents().develop(team, s)
    assert out == {"session": "01a13f6c-eace", "summary": "DONE", "tokens": 29513 + 58}
    argv = (tmp_path / "argv").read_text().splitlines()
    scratch = team.home / "scratch" / "A1a"  # outside the worktree, writable in the sandbox
    assert argv[0] == f"exec --json --sandbox workspace-write -C {repo} --add-dir {scratch} -"
    assert "Task A1a: Shell" in (tmp_path / "stdin").read_text()
    assert f"goes ONLY to\n  {scratch}" in (tmp_path / "stdin").read_text()
    assert "quick set" not in (tmp_path / "stdin").read_text()  # web dev: no benchmark lines
    assert not (tmp_path / "key").exists()  # ChatGPT login only, never the API key

    RealAgents().develop(team, {**s, "session": out["session"], "feedback": "fix the label"})
    argv = (tmp_path / "argv").read_text().splitlines()
    assert argv[1].endswith("resume 01a13f6c-eace -")
    assert "fix the label" in (tmp_path / "stdin").read_text()

    rows = costs.subscription_rows(team.home, costs._today())
    assert [(r[0], r[1], r[2], r[3], r[5], r[6]) for r in rows] == [
        ("A1a", "dev_app", "codex (subscription)", 2, 59026, 116)
    ]


def test_quick_benchmark_brief_only_for_pipeline_and_with_dev_baseline(repo):
    team = team_for(repo, gates=bench_repo(repo, "90"))
    team.fetch("dev")
    s = {"task": {"id": "P3"}}
    devs = team.cfg["developers"]
    assert quick_benchmark(team, s, devs["dev_app"]) == ""
    text = quick_benchmark(team, s, devs["dev_pipeline"])
    assert "--only notre-dame,room" in text and "No cached dev baseline" in text
    G.benchmark(team, team.git("rev-parse", "devteam-fake/dev").strip())
    assert "| room | 90.0 |" in quick_benchmark(team, s, devs["dev_pipeline"])
    s["task"]["quick_only"] = ["notre-dame", "aegviidu"]  # a task picks the videos it needs
    assert "--only notre-dame,aegviidu" in quick_benchmark(team, s, devs["dev_pipeline"])


def test_benchmark_cache_survives_merges_that_leave_pipeline_alone(repo):
    team = team_for(repo, gates=bench_repo(repo, "90"))
    first = git(repo, "rev-parse", "HEAD").strip()
    assert G.benchmark(team, first)["videos"]["room"]["registered_pct"] == 90
    (repo / "web" / "app.ts").write_text("export const x = 1\n")
    git(repo, "commit", "-qam", "web only")
    team.cfg["gates"]["benchmark"]["command"] = "false"  # a re-run would fail
    assert G.benchmark(team, git(repo, "rev-parse", "HEAD").strip()) is not None
    (repo / "pipeline" / "score.txt").write_text("91")
    git(repo, "commit", "-qam", "pipeline change")
    assert G.benchmark(team, git(repo, "rev-parse", "HEAD").strip()) is None  # re-ran, failed


def test_per_video_tolerance():
    v = {"registered_pct": 85, "models": 3, "reproj_px": 0.64, "points": 13384}
    noisy = {**v, "reproj_px": 0.74, "points": 10845}  # two identical runs of mykolaiv
    cfg = CFG["gates"]["benchmark"]
    assert G.regressions({"videos": {"mykolaiv": v}}, {"videos": {"mykolaiv": noisy}}, cfg) == []
    assert len(G.regressions({"videos": {"other": v}}, {"videos": {"other": noisy}}, cfg)) == 2


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


def test_report_shows_subscription_load_per_task(repo):
    team = team_for(repo)
    costs.record(
        team.home,
        "dev_pipeline",
        "claude-code (subscription)",
        "P2a",
        9e5,
        2e4,
        4.2,
        False,
        secs=1800,
    )
    costs.record(
        team.home,
        "dev_pipeline",
        "claude-code (subscription)",
        "P2a",
        1e5,
        1e4,
        0.8,
        False,
        secs=600,
    )
    costs.record(
        team.home, "dev_app", "codex (subscription)", "A1a", 5e5, 3e4, 0.0, False, secs=1200
    )
    text = write_report(team).read_text()
    assert "## Subscription load per task" in text
    assert (
        "| P2a | dev_pipeline | claude-code (subscription) | 2 | 40 | 1,000,000 | 30,000 | 5.00 |"
        in text
    )
    assert "| A1a | dev_app | codex (subscription) | 1 | 20 | 500,000 | 30,000 | — |" in text
    json.dumps(text)


def test_parallel_worktrees_do_not_race_on_git_locks(repo):
    """Seen in the first real v2 run: two `worktree add -b` at once -> .git/config lock."""
    team = team_for(repo)
    git(repo, "remote", "add", "devteam-fake", remote_of(repo))  # like origin: -b sets upstream
    team.fetch("dev")
    errors = []

    def add(name, start):
        start.wait()
        try:
            team.git(
                "worktree", "add", "-q", "-b", name, str(repo.parent / name), "devteam-fake/dev"
            )
        except RuntimeError as e:
            errors.append(str(e))

    for i in range(20):
        start = threading.Barrier(3)
        threads = [threading.Thread(target=add, args=(f"{x}{i}", start)) for x in "abc"]
        [t.start() for t in threads]
        [t.join() for t in threads]
    assert errors == []


def test_a_crashing_parallel_task_says_so_at_once(repo):
    class Crash(FakeAgents):
        delay = 0

        def develop(self, team, s):
            raise RuntimeError("codex failed: usage limit")

    team = team_for(repo)
    team.agents = Crash()
    graph = build_graph(team, SqliteSaver(sqlite3.connect(":memory:", check_same_thread=False)))
    graph.invoke({}, {"configurable": {"thread_id": "r"}})  # both tasks blocked, no raise
    log = (team.home / "events.log").read_text()
    assert "BLOCKED: CRASHED in develop at test_v2.py:" in log
    assert "codex failed: usage limit" in log
