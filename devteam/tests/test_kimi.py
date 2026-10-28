"""Kimi as dev_app (K1): a stand-in `kimi` on PATH talks to a stand-in Moonshot API through
Kerege's proxy; on macOS it runs in the real sandbox."""

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml
from conftest import team_for
from test_supervisor import CODEX_OK

from devteam import costs
from devteam.real import RealAgents
from devteam.supervise import TaskHalted

KEY = "not-a-real-moonshot-credential"
SANDBOX = sys.platform == "darwin"
USAGE = {"prompt_tokens": 1000, "completion_tokens": 200, "cached_tokens": 600}

# Speaks the CLI's stream-json (kimi-code 2.3.3); one chat call to the base_url of its config.
KIMI = """#!/usr/bin/python3
import json, os, pathlib, re, socket, subprocess, sys, time, urllib.error, urllib.request
out = pathlib.Path({out!r})
mode = (out / "mode").read_text().strip()
(out / "argv").write_text(json.dumps(sys.argv[1:]))
cfg = open(os.environ["KIMI_CODE_HOME"] + "/config.toml").read()
pathlib.Path("kimi-saw.txt").write_text(json.dumps(dict(os.environ)) + cfg)  # in the worktree
probes = {{}}
for name, fn in {{
    "write_home": lambda: open({home!r} + "/.kerege-sandbox-probe", "w"),
    "list_home": lambda: os.listdir({home!r}),
    "internet": lambda: socket.create_connection(("1.1.1.1", 443), timeout=3),
}}.items():
    try:
        fn()
        probes[name] = "allowed"
    except OSError:
        probes[name] = "denied"
pathlib.Path("probes.json").write_text(json.dumps(probes))
if mode == "hang":
    subprocess.Popen(["sleep", "3017"])
base = re.search(r'base_url = "(.+)"', cfg).group(1)
body = json.dumps({{"model": "kimi-k3.1-pro", "stream": True, "messages": []}}).encode()
req = urllib.request.Request(base + "/chat/completions", data=body,
                             headers={{"Authorization": "Bearer dummy"}})
try:
    urllib.request.urlopen(req).read()
except urllib.error.HTTPError as e:
    print(json.dumps({{"type": "error", "message": e.read().decode()}}))
    sys.exit(1)
if mode == "hang":
    time.sleep(300)
if mode == "fail":
    print(json.dumps({{"type": "error", "message": "provider error: 429 rate limited"}}))
    sys.exit(1)
print(json.dumps({{"role": "assistant", "content": "editing", "tool_calls": [{{"id": "t1"}}]}}))
print(json.dumps({{"role": "tool", "tool_call_id": "t1", "content": "ok"}}))
print(json.dumps({{"role": "assistant", "content": "DONE"}}))
print(json.dumps({{"role": "meta", "type": "session.resume_hint", "session_id": "s-42"}}))
"""


@pytest.fixture
def moonshot():
    """A stand-in Open Platform: records each call's Authorization, streams one SSE answer."""
    seen = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            seen.append((self.path, self.headers.get("Authorization")))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            last = {"choices": [{"delta": {}, "finish_reason": "stop", "usage": USAGE}]}
            for e in ({"choices": [{"delta": {"content": "hi"}}]}, last):
                self.wfile.write(b"data: " + json.dumps(e).encode() + b"\n\n")
            self.wfile.write(b"data: [DONE]\n\n")

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}", seen
    srv.shutdown()


@pytest.fixture
def kimi_on_path(tmp_path, monkeypatch):
    """`kimi` and `codex` stand-ins; off macOS a pass-through `sandbox-exec` (CI is Linux)."""
    out, bindir = tmp_path / "kimi-out", tmp_path / "bin"
    out.mkdir()
    bindir.mkdir()
    (out / "mode").write_text("ok")
    (bindir / "kimi").write_text(KIMI.format(out=str(out), home=str(Path.home())))
    (bindir / "codex").write_text(
        f"#!/bin/sh\ncat > /dev/null\ntouch {out}/codex-ran\ncat <<'EOF'\n{CODEX_OK}EOF\n"
    )
    if not SANDBOX:
        (bindir / "sandbox-exec").write_text('#!/bin/sh\nshift 2\nexec "$@"\n')
    for f in bindir.iterdir():
        f.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
    monkeypatch.setenv("MOONSHOT_API_KEY", KEY)  # even in Kerege's env it never reaches the CLI
    return out


def kimi_team(repo, upstream, **kimi_cfg):
    team = team_for(repo)
    team.secrets = {"MOONSHOT_API_KEY": KEY}
    team.cfg["providers"]["kimi_cli"].update(upstream=upstream, **kimi_cfg)
    return team


def state(task="A1a", **over):
    return {
        "task": {"id": task, "title": "Shell", "goal": "g"},
        "assignee": "dev_app",
        "plan": "p",
        "criteria": ["c"],
        **over,
    }


def events(team) -> list[str]:
    return [e["msg"] for e in yaml.safe_load((team.home / "events.log").read_text()) or []]


def test_kimi_develops_through_the_proxy_billed_per_call_key_kept(repo, moonshot, kimi_on_path):
    upstream, seen = moonshot
    team = kimi_team(repo, upstream)
    out = RealAgents().develop(team, state(worktree=str(repo)))
    assert out == {"session": "kimi:s-42", "summary": "DONE", "tokens": 1200}
    assert seen == [("/v1/chat/completions", f"Bearer {KEY}")]  # the proxy added the key
    assert KEY not in (repo / "kimi-saw.txt").read_text()  # never in the CLI's env or config
    argv = json.loads((kimi_on_path / "argv").read_text())
    scratch = str(team.home / "scratch" / "A1a")
    assert argv[0] == "-p" and argv[2:] == ["--output-format", "stream-json", "--add-dir", scratch]
    assert "Task A1a: Shell" in argv[1]
    (row,) = costs.day_rows(team.home, costs._today())
    agent, model, calls, usd, billed, tokens_in, cache_read, _cw, stale, prov = row
    assert (agent, model, calls, billed, tokens_in, cache_read, stale, prov) == (
        "dev_app", "kimi-k3.1-pro", 1, 1, 1000, 600, 0, "kimi_cli",
    )  # fmt: skip
    assert usd == pytest.approx((400 * 2.0 + 600 * 0.2 + 200 * 10.0) / 1e6)
    assert costs.kimi_spend(team.home) == (pytest.approx(usd), pytest.approx(usd))
    assert costs.spent_24h(team.home) == 0  # Anthropic's daily_budget_usd is not touched

    RealAgents().develop(team, state(worktree=str(repo), session="kimi:s-42", feedback="fix it"))
    argv = json.loads((kimi_on_path / "argv").read_text())
    assert argv[-2:] == ["--session", "s-42"] and "fix it" in argv[1]


@pytest.mark.skipif(not SANDBOX, reason="sandbox-exec is macOS only; elsewhere Kimi is not used")
def test_kimi_sandbox_keeps_it_in_the_worktree(repo, moonshot, kimi_on_path):
    team = kimi_team(repo, moonshot[0])
    try:
        RealAgents().develop(team, state(worktree=str(repo)))
        probes = json.loads((repo / "probes.json").read_text())
    finally:
        (Path.home() / ".kerege-sandbox-probe").unlink(missing_ok=True)
    assert probes == {"write_home": "denied", "list_home": "denied", "internet": "denied"}


def test_supervisor_kills_a_kimi_round_and_everything_it_started(repo, moonshot, kimi_on_path):
    (kimi_on_path / "mode").write_text("hang")
    team = kimi_team(repo, moonshot[0])
    team.cfg["supervisor"] = {**team.cfg["supervisor"], "max_tokens_per_round": 1000}
    with pytest.raises(TaskHalted, match="this round used"):
        RealAgents().develop(team, state(worktree=str(repo)))
    left = subprocess.run(["pgrep", "-f", "sleep 3017"], capture_output=True, text=True).stdout
    assert left == ""


@pytest.mark.parametrize("mode", ["fail", "budget", "cap"])
def test_dev_app_falls_back_to_codex_in_one_logged_line(repo, moonshot, kimi_on_path, mode):
    """Kimi fails (or hits a limit), its caps are reached before the round, or during it."""
    (kimi_on_path / "mode").write_text("fail" if mode == "fail" else "ok")
    team = kimi_team(repo, moonshot[0], **({"daily_usd": 0.1} if mode == "cap" else {}))
    if mode == "budget":
        costs.record(team.home, "dev_app", "kimi-k3.1-pro", "X", 1, 1, 50.0, True, 0, "kimi_cli")
    out = RealAgents().develop(team, state(worktree=str(repo)))
    assert out["summary"] == "DONE" and out["session"] == "01a13f6c-eace"  # Codex's
    assert (kimi_on_path / "codex-ran").exists()
    assert (kimi_on_path / "argv").exists() == (mode != "budget")  # no Kimi call over a cap
    (line,) = [m for m in events(team) if m.startswith("kimi_cli -> codex_cli: ")]
    assert {"fail": "429 rate limited", "budget": "last 24 h", "cap": "kimi_cli budget"}[
        mode
    ] in line


def test_kimi_is_skipped_without_its_key_or_when_off(repo, moonshot, kimi_on_path):
    team = kimi_team(repo, moonshot[0])
    team.secrets = {}
    RealAgents().develop(team, state(worktree=str(repo)))
    team.secrets = {"MOONSHOT_API_KEY": KEY}
    team.cfg["providers"]["kimi_cli"]["enabled"] = False
    RealAgents().develop(team, state(worktree=str(repo)))
    assert not (kimi_on_path / "argv").exists()
    assert [m for m in events(team) if m.startswith("kimi_cli")] == [
        "kimi_cli -> codex_cli: MOONSHOT_API_KEY is missing in devteam/.env",
        "kimi_cli -> codex_cli: kimi_cli is off in config.yaml",
    ]
