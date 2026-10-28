"""Kimi Code CLI as a developer (Kerege v3 K1, owner 2026-10-28): `kimi -p ... --output-format
stream-json` on the Kimi Open Platform (Moonshot API key, pay per use).

The CLI has no sandbox of its own and its shell tool inherits its environment, so:
- the key never enters the agent's process: Kerege runs a local proxy (Proxy) that adds it, and
  the CLI talks only to the proxy. The proxy also reserves each call's worst case against the
  Kimi caps (24 h and the calendar month) and settles it to the usage Moonshot reports, so the
  spend is counted per call and a cap stops the very next call (the round then falls back);
- the CLI runs under macOS `sandbox-exec`: writes only in the worktree (not its .git), the
  task's scratch folder and its own state; no reads of the home folder outside these (the main
  checkout with devteam/.env and data/, ~/.ssh, other agents' logins); network only to
  localhost. Nothing else can reach Moonshot.
"""

import json
import os
import shutil
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx

from devteam import costs

ALIAS = "kerege"  # the model's name in the CLI's config.toml
MAX_OUT = 32_000  # output tokens a call may produce when it does not say (worst case)
MAX_CONTEXT = 262_144  # caps a call's input: at $2 / M about $0.52 at most
HOP = {"connection", "keep-alive", "transfer-encoding", "content-length", "host", "authorization"}
ENV_KEPT = ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "TERM", "USER", "LOGNAME", "SHELL", "TMPDIR")


def unavailable(team, task: dict, real: bool = True) -> str:
    """Why Kimi cannot take this round ("" when it can); the round then goes to the fallback.
    real=False (fake agents): the key, the machine and the CLI are not needed."""
    p = (team.cfg.get("providers") or {}).get(costs.KIMI) or {}
    if not p.get("enabled"):
        return "kimi_cli is off in config.yaml"
    if real and not team.secrets.get(p["key"]):
        return f"{p['key']} is missing in devteam/.env"
    if task.get("writable"):  # data/ never goes to Moonshot
        return f"the task writes {task['writable']}"
    if real and not shutil.which("sandbox-exec"):  # macOS only
        return "no sandbox-exec on this machine"
    if real and not shutil.which("kimi"):
        return "`kimi` is not on PATH"
    day, month = costs.kimi_spend(team.home)
    if day >= p["daily_usd"]:
        return f"Kimi spent ${day:.2f} in the last 24 h (cap ${p['daily_usd']:.2f})"
    if month >= p["monthly_usd"]:
        return f"Kimi spent ${month:.2f} this month (cap ${p['monthly_usd']:.2f})"
    return ""


def pick(team, name: str, task: dict, real: bool = True) -> dict:
    """The developer config this round runs with: its own, or its `fallback` (one line in the
    events log saying why)."""
    dev = team.cfg["developers"][name]
    if dev["provider"] != costs.KIMI or not (why := unavailable(team, task, real)):
        return dev
    fb = dev["fallback"]
    team.emit(name, f"kimi_cli -> {fb['provider']}: {why}", task=task["id"])
    return {**dev, "model": None, **fb}


class Proxy:
    """http://127.0.0.1:<port>/v1 for one round. Adds the key, keeps Kimi under its caps, counts
    tokens (the supervisor's live counter: feed / usage)."""

    def __init__(self, team, agent: str, task: str, model: str):
        self.p = team.cfg["providers"][costs.KIMI]
        self.key, self.home = team.secrets[self.p["key"]], team.home
        self.agent, self.task, self.model, self.refused = agent, task, model, ""
        self.tokens = [0, 0]  # in (cache reads included), out
        self.lock = threading.Lock()
        proxy = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                proxy.forward(self)

            do_POST = do_GET

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def feed(self, _chunk: bytes) -> int:
        return sum(self.tokens)

    def usage(self) -> dict:
        return {"in": self.tokens[0], "out": self.tokens[1]}

    def _reply(self, h, status: int, body: dict):
        data = json.dumps(body).encode()
        h.send_response(status)
        h.send_header("Content-Type", "application/json")
        h.send_header("Content-Length", str(len(data)))
        h.end_headers()
        h.wfile.write(data)

    def forward(self, h):
        body = h.rfile.read(int(h.headers.get("Content-Length") or 0))
        p, rid = self.p, None
        if h.command == "POST":
            try:
                req = json.loads(body)
            except ValueError:
                req = {}
            out_max = req.get("max_completion_tokens") or req.get("max_tokens") or MAX_OUT
            # every token is at least one byte: the body's bytes bound the input tokens
            max_usd = (len(body) * p["price_in"] + out_max * p["price_out"]) / 1e6
            try:
                rid = costs.reserve(
                    self.home, p["daily_usd"], self.agent, self.model, self.task, max_usd,
                    costs.KIMI, p["monthly_usd"],
                )  # fmt: skip
            except costs.BudgetExceeded as e:  # 403: the CLI does not retry it, the round ends
                self.refused = str(e)
                return self._reply(h, 403, {"error": {"type": "budget", "message": str(e)}})
        headers = {k: v for k, v in h.headers.items() if k.lower() not in HOP}
        headers["Authorization"] = f"Bearer {self.key}"
        buf, usage = bytearray(), None
        # an error mid-call (the CLI killed, the network): billing unknown, the reservation stays
        # at its worst case and becomes stale (costs.STALE_H)
        with httpx.stream(
            h.command, p["upstream"] + h.path, content=body, headers=headers,
            timeout=httpx.Timeout(600, connect=30),
        ) as r:  # fmt: skip
            h.send_response(r.status_code)
            for k, v in r.headers.items():
                if k.lower() not in HOP | {"content-encoding"}:
                    h.send_header(k, v)
            h.end_headers()
            for chunk in r.iter_bytes():
                h.wfile.write(chunk)
                h.wfile.flush()
                usage = _usage(buf, chunk) or usage
            usage = _usage(buf, b"\n") or usage
        if rid is None:
            return
        if r.status_code >= 400:  # refused by the API: not billed
            costs.release(self.home, rid)
        elif usage:
            n_in, cached = usage.get("prompt_tokens") or 0, _cached(usage)
            n_out = usage.get("completion_tokens") or 0
            usd = (
                (n_in - cached) * p["price_in"]
                + cached * p["price_cache_read"]
                + n_out * p["price_out"]
            ) / 1e6
            costs.settle(self.home, rid, n_in, n_out, usd, cache_read=cached)
            with self.lock:
                self.tokens[0] += n_in
                self.tokens[1] += n_out


def _cached(u: dict) -> int:
    details = u.get("prompt_tokens_details") or {}
    return u.get("cached_tokens") or details.get("cached_tokens") or 0


def _usage(buf: bytearray, chunk: bytes) -> dict | None:
    """The last `usage` in whole lines of an SSE stream (`data: {...}`) or a JSON body; Moonshot
    puts it on the last chunk, at the top or in choices[0]."""
    buf += chunk
    *lines, rest = bytes(buf).split(b"\n")
    buf[:] = rest
    found = None
    for line in lines:
        try:
            e = json.loads(line.removeprefix(b"data:").strip() or b"null")
        except ValueError:
            continue
        if isinstance(e, dict):
            choices = [c for c in e.get("choices") or [] if isinstance(c, dict)]
            found = e.get("usage") or next((c["usage"] for c in choices if c.get("usage")), found)
    return found


def profile(writable: list[Path], readable: list[Path], wt: Path) -> str:
    """The sandbox-exec (Seatbelt) profile of the CLI and everything it starts."""

    def q(p) -> str:
        return json.dumps(os.path.realpath(p))

    rw = [*writable, Path("/private/tmp"), Path("/private/var/folders"), Path("/dev")]
    return "\n".join(
        [
            "(version 1)",
            "(allow default)",
            "(deny network-outbound)",
            '(allow network-outbound (remote ip "localhost:*"))',
            f"(deny file-read-data (subpath {q(Path.home())}))",
            "(allow file-read-data " + " ".join(f"(subpath {q(p)})" for p in readable) + ")",
            "(deny file-write*)",
            "(allow file-write* " + " ".join(f"(subpath {q(p)})" for p in rw) + ")",
            f"(deny file-write* (literal {q(wt / '.git')}))",
        ]
    )


def command(team, s: dict, dev: dict, proxy: Proxy, prompt: str, kimi: str) -> tuple[list, dict]:
    """(argv, env) of one round: the CLI in its sandbox, its own state in .state/kimi/<task>."""
    tid, wt = s["task"]["id"], Path(s["worktree"])
    state, scratch = team.home / "kimi" / tid, team.home / "scratch" / tid
    (state / "home").mkdir(parents=True, exist_ok=True)
    (state / "config.toml").write_text(
        f'default_model = "{ALIAS}"\ntelemetry = false\n\n'
        f'[providers.kerege]\ntype = "kimi"\nbase_url = "{proxy.url}"\n'
        'api_key = "kept-by-kerege"  # the real key stays in Kerege\'s proxy\n\n'
        f'[models.{ALIAS}]\nprovider = "kerege"\nmodel = "{dev["model"]}"\n'
        f"max_context_size = {MAX_CONTEXT}\n"
    )
    git_dir = team.git("rev-parse", "--git-common-dir", cwd=str(wt)).strip()
    readable = [wt, scratch, state, (wt / git_dir).resolve()]
    env = {k: v for k, v in os.environ.items() if k in ENV_KEPT}
    env |= {
        "HOME": str(state / "home"),  # git, node and the CLI find no logins or keys of the owner
        "KIMI_CODE_HOME": str(state),
        "KIMI_DISABLE_TELEMETRY": "1",
        "KIMI_CODE_NO_AUTO_UPDATE": "1",
    }
    cmd = [
        "sandbox-exec", "-p", profile([wt, scratch, state], readable, wt),
        kimi, "-p", prompt, "--output-format", "stream-json", "--add-dir", str(scratch),
    ]  # fmt: skip
    session = s.get("session", "")
    if session.startswith("kimi:") and (s.get("feedback") or s.get("conflicts")):
        cmd += ["--session", session.removeprefix("kimi:")]
    return cmd, env


def parse(stdout: str, ok: bool, stderr: str) -> dict:
    """stream-json lines: {role: assistant, content, tool_calls}, {role: tool, ...},
    {role: meta, type: session.resume_hint, session_id} at the end, {type: error} on failure.
    The summary is the last assistant text."""
    summary, err, session = "", "", ""
    for line in stdout.splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if not isinstance(e, dict):
            continue
        if e.get("role") == "assistant" and isinstance(e.get("content"), str):
            summary = e["content"]
        elif e.get("type") == "session.resume_hint":
            session = "kimi:" + e.get("session_id", "")
        elif e.get("type") == "error":
            err = e.get("message") or "error"
    if not ok and not err:
        err = stderr[-800:] or "kimi exited with an error and no message"
    return {"session": session, "summary": err or summary, "error": bool(err) or not session}
