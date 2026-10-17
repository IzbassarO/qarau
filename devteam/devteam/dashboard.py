"""`devteam dashboard`: one local, read-only page (Kerege). Real data only, no model calls:
.state (events log, costs.sqlite, packets, run summaries), the backlog, git log, and gh for PRs
and CI. Every byte of the page goes through Team.redact; nothing from .env is shown."""

import html
import json
import os
import subprocess
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import yaml

from devteam import costs
from devteam.continuous import runs_of_day
from devteam.graph import AGENTS, Team

GH_CACHE_S = 60
EVENTS_TAIL = 400_000  # bytes of events.log read per refresh


def run_active(team: Team) -> bool:
    pid_file = team.home / "run.pid"
    try:
        os.kill(int(pid_file.read_text()), 0)
        return True
    except (FileNotFoundError, ValueError, ProcessLookupError, PermissionError):
        return False


def recent_events(team: Team) -> list[dict]:
    path = team.home / "events.log"
    if not path.exists():
        return []
    with open(path, "rb") as f:
        f.seek(max(0, path.stat().st_size - EVENTS_TAIL))
        text = f.read().decode("utf-8", "replace")
    start = 0 if text.startswith("- agent:") else text.find("\n- agent:") + 1
    if not text.startswith("- agent:", start):
        return []  # no whole event in the tail
    try:
        return yaml.safe_load(text[start:]) or []
    except yaml.YAMLError:  # a write in progress: next refresh
        return []


def agent_states(events: list[dict], active: bool, now: float) -> dict[str, list[dict]]:
    """Per agent: the tasks whose last event says working / waiting, with time on it."""
    last: dict[tuple, dict] = {}
    for e in events:
        if e.get("status") in ("working", "waiting", "idle"):
            last[(e["agent"], e.get("task") or "backlog")] = e
    out: dict[str, list[dict]] = {a: [] for a in AGENTS}
    if not active:
        return out
    for (agent, task), e in last.items():
        if e["status"] == "idle":
            continue
        since = None
        if e.get("ts"):
            since = now - time.mktime(time.strptime(e["ts"], "%Y-%m-%dT%H:%M:%S"))
        out.setdefault(agent, []).append(
            {"task": task, "state": e["status"], "secs": since, "msg": e.get("msg", "")}
        )
    return out


class GhCache:
    """gh calls, cached so a page refresh does not hit GitHub every time."""

    def __init__(self, team: Team, runner=None):
        self.team, self.cache = team, {}
        self.runner = runner or self._run

    def _run(self, *args: str) -> str:
        r = subprocess.run(
            ["gh", *args], cwd=self.team.root, capture_output=True, text=True, timeout=20
        )
        if r.returncode:
            raise RuntimeError(r.stderr.strip()[-300:] or f"gh exited {r.returncode}")
        return r.stdout

    def json(self, *args: str):
        hit = self.cache.get(args)
        if hit and time.time() - hit[0] < GH_CACHE_S:
            return hit[1]
        try:
            val = json.loads(self.runner(*args))
        except Exception as e:  # shown on the page, never fatal
            val = {"error": str(e)}
        self.cache[args] = (time.time(), val)
        return val


def packet_line(team: Team, tid: str) -> str:
    p = team.home / "packets" / f"{tid}.md"
    if not p.exists():
        return ""
    lines = p.read_text().splitlines()
    return lines[2].replace("**", "").replace("`", "") if len(lines) > 2 else ""


def collect(team: Team, gh: GhCache, now: float | None = None) -> dict:
    now = now or time.time()
    day = time.strftime("%Y-%m-%d", time.localtime(now))
    active = run_active(team)
    tasks = team.load_backlog()
    spent, cap = costs.spent_today(team.home), team.cfg["daily_budget_usd"]
    base = team.cfg["base_branch"]
    if team.fake:
        prs, ci = {"error": "fake mode: no GitHub"}, {"error": "fake mode: no GitHub"}
    else:
        prs = gh.json(
            "pr", "list", "--state", "open", "--json",
            "number,title,url,headRefName,baseRefName,author,createdAt,isDraft",
        )  # fmt: skip
        ci = gh.json(
            "run", "list", "--branch", base, "--limit", "20", "--json",
            "workflowName,status,conclusion,url,headSha,createdAt",
        )  # fmt: skip
    try:
        log = team.git("log", "--oneline", "-12", f"{team.remote_name}/{base}").splitlines()
    except RuntimeError as e:
        log = [f"git log failed: {e}"]

    latest_ci: dict[str, dict] = {}
    if isinstance(ci, list):
        for r in ci:  # newest first: the first completed run per workflow
            if r.get("status") == "completed":
                latest_ci.setdefault(r["workflowName"], r)
    busy = {team.branch_for(t["id"]) for t in tasks if t["status"] == "in_progress"}
    attention = []
    for name, r in latest_ci.items():
        if r.get("conclusion") not in ("success", "skipped", "neutral"):
            attention.append(
                ("CI", f"{name} on {base} is {r.get('conclusion')} ({r['headSha'][:8]})", r["url"])
            )
    for t in tasks:
        if t["status"] == "blocked":
            attention.append(
                ("blocked", f"{t['id']} {t['title']}: {t.get('blocked_reason', '')}", "")
            )
    if isinstance(prs, list):
        for p in prs:
            if p["headRefName"] not in busy:  # devteam is still working on the others
                attention.append(
                    ("PR", f"#{p['number']} {p['title']} → {p['baseRefName']}", p["url"])
                )
    if cap and spent > 0.8 * cap:
        attention.append(("spend", f"API spend ${spent:.2f} is above 80% of ${cap:.2f}", ""))

    runs = runs_of_day(team.home, day)
    return {
        "now": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
        "active": active,
        "agents": agent_states(recent_events(team), active, now),
        "spent": spent,
        "budget": cap,
        "cost_rows": costs.day_rows(team.home, day),
        "sub_rows": costs.subscription_rows(team.home, day),
        "limits": costs.limit_rows(
            team.home, time.strftime("%Y-%m-%d", time.localtime(now - 7 * 86400))
        ),
        "tasks": [{**t, "packet": packet_line(team, t["id"])} for t in tasks],
        "prs": prs,
        "ci": latest_ci if isinstance(ci, list) else ci,
        "log": log,
        "attention": attention,
        "last_run": runs[-1] if runs else None,
    }


# --- page --------------------------------------------------------------------------------
CSS = """
:root{--bg:#0b0d10;--panel:#14171c;--line:#262b33;--ink:#e6e9ee;--ink2:#9aa3ad;--red:#f0565c;
--orange:#f2a33a;--blue:#4f8cff;--green:#3fbf7f}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 ui-sans-serif,system-ui,
-apple-system,sans-serif}
main{max-width:1200px;margin:0 auto;padding:16px}
h1{font-size:18px;margin:0 0 4px}h2{font-size:13px;text-transform:uppercase;letter-spacing:.06em;
color:var(--ink2);margin:24px 0 8px}
.muted{color:var(--ink2)}section{background:var(--panel);border:1px solid var(--line);
border-radius:8px;padding:12px 14px;margin-bottom:12px;overflow-x:auto}
table{border-collapse:collapse;width:100%}td,th{text-align:left;padding:4px 8px;
border-bottom:1px solid var(--line);vertical-align:top}th{color:var(--ink2);font-weight:500}
a{color:var(--blue)}.red{color:var(--red)}.orange{color:var(--orange)}.green{color:var(--green)}
.bar{height:8px;background:var(--line);border-radius:4px;overflow:hidden;margin-top:6px}
.bar>div{height:100%;background:var(--blue)}.bar>div.hot{background:var(--red)}
.mono{font-family:ui-monospace,Menlo,monospace;font-size:12px}
.att li{margin:4px 0}.tag{display:inline-block;min-width:56px;color:var(--orange)}
"""


def _e(x) -> str:
    return html.escape(str(x if x is not None else ""))


def _link(url: str, text: str) -> str:
    if not url or not url.startswith(("https://", "http://", "fake://")):
        return _e(text)
    return f'<a href="{_e(url)}" rel="noreferrer">{_e(text)}</a>'


def _dur(secs) -> str:
    if secs is None:
        return "—"
    m = int(secs // 60)
    return f"{m // 60} h {m % 60:02d} min" if m >= 60 else f"{m} min"


def render(d: dict) -> str:
    p = [
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width,initial-scale=1'>",
        "<meta http-equiv='refresh' content='20'><title>Kerege dashboard</title>",
        f"<style>{CSS}</style></head><body><main>",
        "<h1>Kerege</h1>",
        f"<p class='muted'>{_e(d['now'])} · run "
        + ("<b class='green'>active</b>" if d["active"] else "not running")
        + " · refreshes every 20 s · read-only</p>",
    ]
    # needs your attention
    p.append("<h2>Needs your attention</h2><section>")
    if d["attention"]:
        p.append("<ul class='att'>")
        p += [
            f"<li><span class='tag'>{_e(k)}</span> {_link(u, t)}</li>" for k, t, u in d["attention"]
        ]
        p.append("</ul>")
    else:
        p.append("<p class='muted'>Nothing.</p>")
    p.append("</section>")
    # agents
    p.append("<h2>Agents</h2><section><table><tr><th>Agent</th><th>Status</th><th>Task</th>"
             "<th>Time on it</th><th>Last message</th></tr>")  # fmt: skip
    for agent, items in d["agents"].items():
        if not items:
            p.append(
                f"<tr><td>{_e(agent)}</td><td class='muted'>idle</td>" + "<td></td>" * 3 + "</tr>"
            )
        for it in items:
            cls = "orange" if it["state"] == "waiting" else "green"
            label = "waiting for limit reset" if it["state"] == "waiting" else "working"
            p.append(
                f"<tr><td>{_e(agent)}</td><td class='{cls}'>{label}</td><td>{_e(it['task'])}</td>"
                f"<td>{_dur(it['secs'])}</td><td class='muted'>{_e(it['msg'][:160])}</td></tr>"
            )
    p.append("</table></section>")
    # spend
    pct = d["spent"] / d["budget"] * 100 if d["budget"] else 0
    p.append(
        "<h2>API spend today</h2><section>"
        f"<b>${d['spent']:.2f}</b> of ${d['budget']:.2f} ({pct:.0f} %)"
        f"<div class='bar'><div class='{'hot' if pct > 80 else ''}' "
        f"style='width:{min(pct, 100):.0f}%'></div></div>"
        "<table><tr><th>Agent</th><th>Model</th><th>Calls</th><th>$</th><th>Billing</th></tr>"
    )
    p += [
        f"<tr><td>{_e(a)}</td><td>{_e(m)}</td><td>{n}</td>"
        + (f"<td>{usd:.3f}</td>" if billed else f"<td class='muted'>≈ {usd:.2f}, not billed</td>")
        + f"<td>{'API' if billed else 'subscription'}</td></tr>"
        for a, m, n, usd, billed in d["cost_rows"]
    ]
    p.append("</table></section>")
    p.append("<h2>Subscription load per task (today)</h2><section><table><tr><th>Task</th>"
             "<th>Developer</th><th>Tool</th><th>Rounds</th><th>Minutes</th><th>Tokens in</th>"
             "<th>Tokens out</th></tr>")  # fmt: skip
    p += [
        f"<tr><td>{_e(t)}</td><td>{_e(a)}</td><td>{_e(m)}</td><td>{n}</td>"
        f"<td>{(secs or 0) / 60:.0f}</td><td>{ti or 0:,}</td><td>{to or 0:,}</td></tr>"
        for t, a, m, n, secs, ti, to, _usd in d["sub_rows"]
    ] or ["<tr><td colspan='7' class='muted'>No developer runs today.</td></tr>"]
    p.append("</table></section>")
    # limits
    p.append("<h2>Subscription limits (7 days)</h2><section><table><tr><th>When</th>"
             "<th>Developer</th><th>Task</th><th>Resets</th><th>Waited</th>"
             "<th>Message</th></tr>")  # fmt: skip
    p += [
        f"<tr><td>{_e(ts.replace('T', ' '))}</td><td>{_e(a)}</td><td>{_e(t)}</td><td>"
        + (_e(time.strftime("%Y-%m-%d %H:%M", time.localtime(r))) if r else "unknown")
        + f"</td><td>{(w or 0) / 60:.0f} min</td><td class='muted'>{_e(msg)}</td></tr>"
        for ts, a, t, r, w, msg in d["limits"]
    ] or ["<tr><td colspan='6' class='muted'>None.</td></tr>"]
    p.append("</table></section>")
    # queue
    p.append("<h2>Queue</h2>")
    for status in ("in_progress", "todo", "blocked", "done"):
        rows = [t for t in d["tasks"] if t["status"] == status]
        p.append(f"<section><b>{_e(status.replace('_', ' '))}</b> "
                 f"<span class='muted'>({len(rows)})</span><table>")  # fmt: skip
        for t in rows:
            pr = t.get("pr") or ""
            extra = (
                _link(pr, "PR #" + pr.rsplit("/", 1)[-1])
                if pr
                else _e(t.get("blocked_reason") or ", ".join(t.get("depends_on") or []))
            )
            p.append(
                f"<tr><td class='mono'>{_e(t['id'])}</td><td>{_e(t['title'])}</td>"
                f"<td>{_e(t.get('owner', ''))}</td><td>{extra}</td>"
                f"<td class='muted'>{_e(t['packet'])}</td></tr>"
            )
        p.append("</table></section>")
    # PRs, CI, git log, last run
    p.append("<h2>Open pull requests</h2><section>")
    if isinstance(d["prs"], dict):
        p.append(f"<p class='muted'>gh: {_e(d['prs'].get('error'))}</p>")
    else:
        p.append("<table>")
        p += [
            f"<tr><td>{_link(x['url'], '#' + str(x['number']))}</td><td>{_e(x['title'])}</td>"
            f"<td class='mono'>{_e(x['headRefName'])} → {_e(x['baseRefName'])}</td></tr>"
            for x in d["prs"]
        ] or ["<tr><td class='muted'>None.</td></tr>"]
        p.append("</table>")
    p.append("</section><h2>CI on the base branch</h2><section>")
    if isinstance(d["ci"], dict) and "error" in d["ci"]:
        p.append(f"<p class='muted'>gh: {_e(d['ci']['error'])}</p>")
    else:
        p.append("<table>")
        p += [
            f"<tr><td>{_e(name)}</td>"
            f"<td class='{'green' if r.get('conclusion') == 'success' else 'red'}'>"
            f"{_e(r.get('conclusion'))}</td><td class='mono'>{_e(r['headSha'][:8])}</td>"
            f"<td>{_link(r['url'], 'run')}</td></tr>"
            for name, r in d["ci"].items()
        ] or ["<tr><td class='muted'>No completed runs.</td></tr>"]
        p.append("</table>")
    p.append("</section><h2>Latest commits on the base branch</h2><section class='mono'>")
    p += [f"<div>{_e(line)}</div>" for line in d["log"]]
    p.append("</section>")
    if r := d["last_run"]:
        p.append(
            "<h2>Last continuous run today</h2><section>"
            f"{_e(r['run'])} · {_e(r['started'][11:16])}–{_e(r['ended'][11:16])} · "
            f"stopped because: <b>{_e(r['stop_reason'])}</b> · tasks: "
            + _e(", ".join(f"{t['id']} {t['status']}" for t in r["tasks"]) or "none")
            + "</section>"
        )
    p.append("</main></body></html>")
    return "".join(p)


def page(team: Team, gh: GhCache) -> str:
    return team.redact(render(collect(team, gh)))


def make_server(team: Team, port: int, gh: GhCache | None = None) -> ThreadingHTTPServer:
    gh = gh or GhCache(team)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # the only method: read-only
            if self.path.split("?")[0] != "/":
                self.send_error(404)
                return
            body = page(team, gh).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # quiet terminal
            pass

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)  # local only


def serve(team: Team, port: int):
    server = make_server(team, port)
    print(f"Kerege dashboard: http://127.0.0.1:{server.server_port}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
