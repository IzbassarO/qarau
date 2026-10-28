"""`devteam dashboard`: one local, read-only page (Kerege). Real data only, no model calls:
.state (events log, costs.sqlite, packets, run summaries), the backlog, git log, and gh for PRs
and CI. Every byte of the page goes through Team.redact; nothing from .env is shown."""

import html
import json
import os
import re
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import yaml

from devteam import costs
from devteam import watchdog as W
from devteam.continuous import runs_of_day
from devteam.graph import AGENTS, OWNER_STOP, Team, waits_on_dependencies
from devteam.report import metric_lines, metrics

GH_CACHE_S = 60
EVENTS_TAIL = 400_000  # bytes of events.log read per refresh


def run_active(team: Team) -> bool:
    return run_started(team) is not None


def run_started(team: Team) -> float | None:
    """When the active run started (run.pid is written at start), None if no run is active."""
    pid_file = team.home / "run.pid"
    try:
        os.kill(int(pid_file.read_text()), 0)
        return pid_file.stat().st_mtime
    except (FileNotFoundError, ValueError, ProcessLookupError, PermissionError):
        return None


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


def _epoch(ts: str) -> float:
    return time.mktime(time.strptime(ts, "%Y-%m-%dT%H:%M:%S"))


def agent_states(
    events: list[dict], started: float | None, now: float, finished: set[str]
) -> dict[str, list[dict]]:
    """Per agent: the tasks whose last event *of the current run* says working / waiting, with
    time on it. An older run's last word (a crash leaves "working") never counts, and a task
    the backlog says is not in progress (`finished`: todo, done, blocked) is never shown as worked
    on (P0, A2a on 2026-10-17)."""
    out: dict[str, list[dict]] = {a: [] for a in AGENTS}
    if started is None:
        return out
    last: dict[tuple, dict] = {}
    for e in events:
        if e.get("status") in ("working", "waiting", "idle") and e.get("ts"):
            if _epoch(e["ts"]) >= int(started):  # ts has whole seconds
                last[(e["agent"], e.get("task") or "backlog")] = e
    for (agent, task), e in last.items():
        if e["status"] == "idle" or task in finished:
            continue
        since = now - _epoch(e["ts"])
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


ROOMS = {"brain": "Lead", "dev_pipeline": "Core", "dev_app": "App", "tester": "Testing"}
ROLE = {
    "brain": "plans the next task",
    "dev_pipeline": "pipeline, backend, infra",
    "dev_app": "web app",
    "tester": "checks, judge, gates",
}
STATES = ("working", "waiting", "blocked", "idle")
OWNER_WAIT = re.compile(
    r"\bwaits? for (the |your )?owner|\bowner'?s (call|decision|approval)", re.I
)
VIOLATION_DO = {
    "out_of_scope": "Split the task in devteam/backlog.yaml: one task per developer.",
    "protected": "Check the branch: no agent may change these files. Resume only after fixing it.",
    "bypass": "Nothing yet: it went back to the developer. A second time blocks it.",
}


def blocked_action(t: dict, by_id: dict) -> str | None:
    """What the owner should do about a blocked task; None when it is not the owner's move."""
    tid, reason = t["id"], t.get("blocked_reason") or ""
    if waits_on_dependencies(t):
        return None  # back to todo by itself when its dependencies merge
    if t.get("owner_only"):  # P4a-2a-run: never `devteam resume` it
        return (
            "Owner-only: you do it yourself in an interactive Claude Code session; Kerege never "
            "plans or resumes it."
        )
    if reason.startswith("supervisor"):
        return (
            "Raise the limit in devteam/config.yaml (supervisor) or split the task, then run "
            f"`devteam resume {tid}`."
        )
    if reason.startswith(OWNER_STOP):
        return f"You stopped it. Run `devteam resume {tid}` when it should go on."
    if reason.startswith("needs a split"):
        return (
            "Split it in devteam/backlog.yaml: one task per developer, at most 3 parts each, "
            "with depends_on."
        )
    if OWNER_WAIT.search(reason):
        return f"It waits for your decision. Once made, run `devteam resume {tid}`."
    return (
        f"Read devteam/.state/packets/{tid}.md, fix the cause (task text, dev, limits), then "
        f"run `devteam resume {tid}`."
    )


def timeline(events: list[dict], day: str, now: float, active: bool) -> dict:
    """Today's working / waiting stretches per agent, from the events log."""
    today = [e for e in events if str(e.get("ts", "")).startswith(day)]
    rows: dict[str, list[dict]] = {a: [] for a in AGENTS}
    open_: dict[tuple, dict] = {}
    for e in today:
        st, key = e.get("status"), (e["agent"], e.get("task") or "backlog")
        if st not in ("working", "waiting", "idle"):
            continue
        at = _epoch(e["ts"])
        if key in open_:
            seg = open_.pop(key)
            seg["end"] = at
            rows.setdefault(key[0], []).append(seg)
        if st != "idle":
            open_[key] = {"task": key[1], "state": st, "start": at, "end": None}
    last = _epoch(today[-1]["ts"]) if today else now
    for (agent, _task), seg in open_.items():
        seg["end"], seg["open"] = (now if active else last), True
        rows.setdefault(agent, []).append(seg)
    start = min((s["start"] for r in rows.values() for s in r), default=now - 3600)
    start = min(start, now - 3600)
    return {"start": start, "end": now, "rows": rows}


def collect(team: Team, gh: GhCache, now: float | None = None) -> dict:
    now = now or time.time()
    day = time.strftime("%Y-%m-%d", time.localtime(now))
    started = run_started(team)
    active = started is not None
    tasks = team.load_backlog()
    by_id = {t["id"]: t for t in tasks}
    spent, cap = costs.spent_24h(team.home), team.cfg["daily_budget_usd"]
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

    def need(kind: str, text: str, do: str, url: str = ""):
        attention.append({"kind": kind, "text": text, "do": do, "url": url})

    for name, r in latest_ci.items():
        if r.get("conclusion") not in ("success", "skipped", "neutral"):
            need(
                "CI",
                f"{name} on {base} is {r.get('conclusion')} ({r['headSha'][:8]})",
                f"Open the run and get {base} green: Kerege merges nothing on a red {base}.",
                r["url"],
            )
    for t in tasks:
        if t["status"] == "blocked" and (do := blocked_action(t, by_id)):
            need("blocked", f"{t['id']} {t['title']}: {t.get('blocked_reason', '')}", do)
    if isinstance(prs, list):
        for p in prs:
            if p["headRefName"] not in busy:  # devteam is still working on the others
                need(
                    "PR",
                    f"#{p['number']} {p['title']} → {p['baseRefName']}",
                    "Review it, then merge or close it.",
                    p["url"],
                )
    violations = team.violations(day)
    for v in violations:  # the scope gate refused something today
        need(
            v["kind"].replace("_", " "),
            f"{v['task']} ({v['agent']}): {v['detail'][:300]}",
            VIOLATION_DO.get(v["kind"], "Look at the task's diff."),
        )
    if cap and spent > 0.8 * cap:
        need(
            "spend",
            f"API spend ${spent:.2f} in the last 24 h is above 80% of ${cap:.2f}",
            "Raise daily_budget_usd in devteam/config.yaml, or let Kerege stop at the cap.",
        )

    hb = W.heartbeat_age(team, now)  # a run whose pid file is left behind died without cleanup
    supposed = active or (team.home / "run.pid").exists()
    hb_stale = hb is not None and hb > W.HEARTBEAT_STALE_S and supposed
    if hb_stale:
        need(
            "heartbeat",
            f"no heartbeat from Kerege for {_ago(hb)} while a run is active",
            "Look at its terminal. If it is gone: `devteam resume` (continuous: `devteam run "
            "--continuous`).",
        )
    crash = team.home / "last_crash.json"
    beat_at = now - hb if hb is not None else 0.0  # a run that beat since then resolved it
    if not active and crash.exists() and crash.stat().st_mtime > beat_at:
        try:
            c = json.loads(crash.read_text())
        except (OSError, json.JSONDecodeError):
            c = {}
        where = f" at {c['where']}" if c.get("where") else ""
        need(
            "crash",
            f"Kerege crashed {c.get('at', '')[:16].replace('T', ' ')}: "
            f"{c.get('error', '?')[:300]}{where} (attempt {c.get('attempt', 1)})",
            "Fix the cause, then `devteam resume` (continuous: `devteam run --continuous`). "
            "Details: devteam/.state/last_crash.json",
        )
    events = recent_events(team)
    agents = agent_states(
        events, started, now, {t["id"] for t in tasks if t["status"] != "in_progress"}
    )
    limits = costs.limit_rows(team.home, time.strftime("%Y-%m-%d", time.localtime(now - 7 * 86400)))
    sub_rows = costs.subscription_rows(team.home, day)
    used = costs.agent_tokens(team.home, day)
    models = team.cfg.get("models", {})
    devs = team.cfg["developers"]
    office = {}
    for a in AGENTS:
        mine = [e for e in events if e.get("agent") == a and str(e.get("ts", "")).startswith(day)]
        items = agents.get(a) or []
        state, task, secs, msg = "idle", "", None, mine[-1]["msg"] if mine else ""
        if items:
            it = next((x for x in items if x["state"] == "working"), items[0])
            state, task, secs, msg = it["state"], it["task"], it["secs"], it["msg"]
        else:
            last_task = next((e.get("task") for e in reversed(mine) if e.get("task")), "")
            if by_id.get(last_task, {}).get("status") == "blocked":
                state, task = "blocked", last_task
        reset = (
            next((r[3] for r in limits if r[1] == a and r[3]), None) if state == "waiting" else None
        )
        m = devs.get(a) or models.get(a, {})
        tool = " · ".join(x for x in (m.get("provider"), m.get("model")) if x)
        office[a] = {
            "room": ROOMS.get(a, a),
            "role": ROLE.get(a, ""),
            "tool": tool,
            "state": state,
            "task": task,
            "secs": secs,
            "msg": msg,
            "reset": reset,
            "tokens": used.get(a, 0),
            "last": [
                {"t": e.get("t", ""), "task": e.get("task", ""), "msg": e["msg"]} for e in mine[-6:]
            ],
        }

    runs = runs_of_day(team.home, day)
    last_run = runs[-1] if runs else None
    if active:
        waiting = [a for a in office.values() if a["state"] == "waiting"]
        working = [a for a in office.values() if a["state"] == "working"]
        run = {
            "state": "waiting for a limit reset" if waiting and not working else "running",
            "reason": "",
            "secs": now - started,
        }
    elif last_run:
        secs = _epoch(last_run["ended"]) - _epoch(last_run["started"])
        run = {"state": "stopped", "reason": last_run["stop_reason"], "secs": secs}
    else:
        run = {"state": "not running", "reason": "no run today", "secs": None}
    task_tokens: dict[str, int] = {}
    for t, _a, _m, _n, _secs, ti, to, _usd in sub_rows:
        task_tokens[t] = task_tokens.get(t, 0) + (ti or 0) + (to or 0)
    return {
        "now": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
        "epoch": now,
        "active": active,
        "run": run,
        "agents": agents,
        "office": office,
        "spent": spent,
        "budget": cap,
        "cost_rows": costs.day_rows(team.home, day),
        "kimi": (*costs.kimi_spend(team.home), (team.cfg.get("providers") or {}).get(costs.KIMI)),
        "sub_rows": sub_rows,
        "sub_tokens": sum(task_tokens.values()),
        "task_tokens": sorted(task_tokens.items(), key=lambda kv: -kv[1]),
        "task_limit": team.cfg.get("supervisor", {}).get("max_tokens_per_task"),
        "limits": limits,
        "tasks": [{**t, "packet": packet_line(team, t["id"])} for t in tasks],
        "done_today": [t["id"] for t in tasks if t.get("done_at") == day],
        "timeline": timeline(events, day, now, active),
        "prs": prs,
        "ci": latest_ci if isinstance(ci, list) else ci,
        "log": log,
        "attention": attention,
        "violations": violations,
        "metrics": metrics(team, now),
        "last_run": last_run,
        "heartbeat": hb,
        "hb_stale": hb_stale,
    }


# --- page --------------------------------------------------------------------------------
# Steppe night: deep blue surfaces; status colours (working, waiting, blocked, idle) are the
# only saturated ones and always come with a word. System fonts: the page works offline.
CSS = """
:root{--bg:#0e1621;--panel:#152130;--raise:#1b2a3d;--line:#243245;--ink:#e4e9f0;--ink2:#93a1b4;
--ink3:#66768b;--working:#4fb3a0;--waiting:#e0a645;--blocked:#e2655b;--idle:#6b7a8f;
--bar:#5b8fd9;--focus:#9cc3ff;color-scheme:dark}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;
font-variant-numeric:tabular-nums}
main{max-width:1280px;margin:0 auto;padding:16px 20px 48px}
a{color:#8fb8f2}a:focus-visible,button:focus-visible{outline:2px solid var(--focus);
outline-offset:2px}
code{font:12px/1.4 ui-monospace,Menlo,monospace;background:var(--raise);padding:1px 5px;
border-radius:4px}
header{display:flex;align-items:baseline;gap:16px;flex-wrap:wrap;margin-bottom:14px}
h1{font-size:20px;font-weight:650;margin:0;letter-spacing:.01em}
h2{font-size:15px;font-weight:600;margin:28px 0 10px}
.muted{color:var(--ink2)}.faint{color:var(--ink3)}
nav{display:flex;gap:4px;margin-left:auto}
nav button{background:none;border:1px solid var(--line);color:var(--ink2);border-radius:6px;
padding:5px 14px;font:inherit;cursor:pointer}
nav button[aria-selected=true]{background:var(--raise);color:var(--ink);border-color:#38506d}
.summary{display:grid;grid-template-columns:1.4fr repeat(4,1fr);border:1px solid var(--line);
border-radius:10px;background:var(--panel)}
.summary>div{padding:12px 16px;border-left:1px solid var(--line);min-width:0}
.summary>div:first-child{border-left:0}
.summary .k{color:var(--ink2);font-size:12.5px}
.summary .v{font-size:22px;font-weight:600;line-height:1.25;margin-top:2px}
.summary .s{color:var(--ink2);font-size:12.5px;overflow-wrap:anywhere}
.summary .s.bad{color:var(--blocked);font-weight:600}
.meter{height:6px;background:var(--line);border-radius:3px;overflow:hidden;margin-top:8px}
.meter>i{display:block;height:100%;background:var(--bar)}.meter>i.hot{background:var(--blocked)}
.dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:6px;
vertical-align:baseline;background:var(--idle)}
.working .dot,.dot.working{background:var(--working)}
.waiting .dot,.dot.waiting{background:var(--waiting)}
.blocked .dot,.dot.blocked{background:var(--blocked)}
.att{list-style:none;margin:0;padding:0;border:1px solid #4a3a2a;border-radius:10px;
background:#1d1f24;overflow:hidden}
.att li{padding:10px 16px;border-top:1px solid var(--line);display:grid;
grid-template-columns:70px 1fr;gap:4px 12px}
.att li:first-child{border-top:0}
.att .kind{color:var(--waiting);font-weight:600;font-size:13px}
.att .do{grid-column:2;color:var(--ink)}
.att .what{color:var(--ink2);overflow-wrap:anywhere}
.calm{border:1px solid var(--line);border-radius:10px;padding:12px 16px;color:var(--ink2);
background:var(--panel)}
.cards{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}
.card{background:var(--panel);border:1px solid var(--line);border-left:4px solid var(--idle);
border-radius:8px;padding:10px 12px;min-width:0}
.card.working{border-left-color:var(--working)}.card.waiting{border-left-color:var(--waiting)}
.card.blocked{border-left-color:var(--blocked)}
.card .name{font-weight:600}.card .tool{color:var(--ink3);font-size:12px}
.card .msg{color:var(--ink2);font-size:12.5px;margin-top:6px;display:-webkit-box;
-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden;overflow-wrap:anywhere}
.cols{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}
.col{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:8px 10px;
min-width:0}
.col h3{font-size:13px;font-weight:600;margin:2px 0 8px;color:var(--ink2)}
.task{border-top:1px solid var(--line);padding:6px 0;font-size:13px}
.task b{font-weight:600}.task .why{color:var(--ink2);font-size:12px;overflow-wrap:anywhere;
display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
.lanes{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:10px 12px}
.lane{display:grid;grid-template-columns:110px 1fr;align-items:center;height:30px}
.track{position:relative;height:16px;background:#111b28;border-radius:3px}
.seg{position:absolute;top:0;height:16px;border-radius:3px;min-width:3px}
.seg.working{background:var(--working)}.seg.waiting{background:var(--waiting)}
.ticks{position:relative;height:18px;margin-left:110px;color:var(--ink3);font-size:11.5px}
.ticks span{position:absolute;transform:translateX(-50%)}
.legend{display:flex;gap:16px;color:var(--ink2);font-size:12.5px;margin-top:6px}
.bars{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:10px 12px}
.brow{display:grid;grid-template-columns:70px 1fr 90px;gap:10px;align-items:center;height:26px}
.btrack{position:relative;height:12px}
.btrack>i{position:absolute;left:0;top:0;height:12px;background:var(--bar);
border-radius:0 4px 4px 0}
.btrack>.cap{position:absolute;top:-6px;bottom:-6px;border-left:2px dashed var(--ink2)}
.num{text-align:right;color:var(--ink2);font-size:12.5px}
table{border-collapse:collapse;width:100%}td,th{text-align:left;padding:5px 8px;
border-bottom:1px solid var(--line);vertical-align:top}th{color:var(--ink2);font-weight:500}
.box{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:8px 12px;
overflow-x:auto}
.two{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.nowrap{white-space:nowrap}.mono{font-family:ui-monospace,Menlo,monospace;font-size:12px}
/* Office: a kerege (yurt lattice) wall behind each room */
.rooms{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}
.room{all:unset;cursor:pointer;display:block;border:1px solid var(--line);border-radius:10px;
overflow:hidden;background:var(--panel)}
.room:focus-visible{outline:2px solid var(--focus);outline-offset:2px}
.room[aria-pressed=true]{border-color:#5d7ea8}
.wall{height:150px;background-color:#16263a;background-image:
linear-gradient(to top,#132030 0 22px,transparent 22px),
repeating-linear-gradient(45deg,transparent 0 17px,#1f3350 17px 19px),
repeating-linear-gradient(-45deg,transparent 0 17px,#1f3350 17px 19px)}
.wall svg{display:block;width:100%;height:150px}
.plate{padding:8px 12px 10px}.plate .dept{font-weight:650;font-size:15px}
.plate .who{color:var(--ink2);font-size:12.5px}
.who-panel{margin-top:12px;background:var(--panel);border:1px solid var(--line);border-radius:10px;
padding:12px 16px}
.who-panel dl{display:grid;grid-template-columns:120px 1fr;gap:4px 12px;margin:8px 0}
.who-panel dt{color:var(--ink2)}.who-panel dd{margin:0;overflow-wrap:anywhere}
.log li{color:var(--ink2);font-size:13px;overflow-wrap:anywhere}
[hidden]{display:none!important}
@media (max-width:1000px){.summary{grid-template-columns:repeat(2,1fr)}
.summary>div{border-left:0;border-top:1px solid var(--line)}
.cards,.cols,.rooms{grid-template-columns:repeat(2,minmax(0,1fr))}.two{grid-template-columns:1fr}}
@media (max-width:560px){.cards,.cols,.rooms,.summary{grid-template-columns:1fr}}
"""

JS = """
(function(){
  function show(tab, who){
    document.querySelectorAll('[data-tab]').forEach(function(s){s.hidden = s.dataset.tab !== tab});
    document.querySelectorAll('nav button').forEach(function(b){
      b.setAttribute('aria-selected', b.dataset.go === tab)});
    document.querySelectorAll('.who-panel').forEach(function(p){p.hidden = p.dataset.who !== who});
    document.querySelectorAll('.room').forEach(function(r){
      r.setAttribute('aria-pressed', r.dataset.who === who)});
  }
  function route(){
    var h = location.hash.slice(1).split('/');
    show(h[0] === 'office' ? 'office' : 'overview', h[1] || '');
  }
  document.querySelectorAll('nav button').forEach(function(b){
    b.onclick = function(){ location.hash = b.dataset.go }});
  document.querySelectorAll('.room').forEach(function(r){
    r.onclick = function(){
      var on = r.getAttribute('aria-pressed') === 'true';
      location.hash = 'office/' + (on ? '' : r.dataset.who)}});
  window.onhashchange = route; route();
  function tick(){
    var now = Date.now() / 1000;
    document.querySelectorAll('[data-reset]').forEach(function(el){
      var left = Math.round(+el.dataset.reset - now);
      if (left <= 0) { el.textContent = 'reset time passed'; return }
      var h = Math.floor(left / 3600), m = Math.floor(left % 3600 / 60), s = left % 60;
      el.textContent = 'resets in ' + (h ? h + ' h ' : '') + m + ' min' + (h ? '' : ' ' + s + ' s');
    });
  }
  tick(); setInterval(tick, 1000);
  setTimeout(function(){ location.reload() }, 20000);  /* keeps the tab (hash) */
})();
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


def _ago(secs: float) -> str:
    return f"{secs:.0f} s" if secs < 120 else _dur(secs)


def _tok(n) -> str:
    n = n or 0
    return f"{n / 1e6:.1f} M" if n >= 1e6 else f"{n / 1e3:.0f} k" if n >= 1e3 else str(n)


def _hm(epoch) -> str:
    return time.strftime("%H:%M", time.localtime(epoch)) if epoch else "unknown"


def _when(ts: str, now: str) -> str:
    """'14:37' today, '10-16 14:37' on another day."""
    return ts[11:16] if ts[:10] == now[:10] else f"{ts[5:10]} {ts[11:16]}"


def _code(text: str) -> str:
    """`backticks` in owner instructions become <code>."""
    parts = _e(text).split("`")
    return "".join(f"<code>{p}</code>" if i % 2 else p for i, p in enumerate(parts))


NO_MSG = "<span class='faint'>No messages today.</span>"
STATE_WORD = {
    "working": "working",
    "waiting": "waiting for limit reset",
    "blocked": "blocked",
    "idle": "idle",
}


def figure(a: dict) -> str:
    """A small person at a desk; posture, screen and sign show the real state."""
    st = a["state"]
    screen = {"working": "#1d4a45", "waiting": "#2a2a22", "blocked": "#4a2323", "idle": "#121a24"}
    body, head, ink = "#8fa3bd", "#c9d4e3", "#0e1621"
    if st in ("idle", "waiting"):
        body, head = "#6f8096", "#9eadc0"
    p = [
        "<svg viewBox='0 0 200 150' role='img' aria-hidden='true'>",
        # desk + monitor + keyboard
        "<rect x='92' y='88' width='96' height='7' rx='2' fill='#5a4636'/>",
        "<rect x='98' y='95' width='5' height='33' fill='#46372b'/>",
        "<rect x='177' y='95' width='5' height='33' fill='#46372b'/>",
        f"<rect x='128' y='50' width='50' height='34' rx='3' fill='{screen[st]}' "
        "stroke='#2f4258' stroke-width='2'/>",
        "<rect x='150' y='84' width='6' height='4' fill='#2f4258'/>",
    ]
    if st == "working":
        p += [
            f"<rect x='{x}' y='{y}' width='{w}' height='3' rx='1.5' fill='#4fb3a0'/>"
            for x, y, w in ((134, 57, 30), (134, 64, 22), (140, 71, 26), (134, 78, 16))
        ]
    if st == "blocked":
        p.append(
            "<path d='M144 59l18 18M162 59l-18 18' stroke='#e2655b' stroke-width='3' "
            "stroke-linecap='round'/>"
        )
    p.append("<rect x='112' y='84' width='24' height='4' rx='1' fill='#3a4d63'/>")
    # chair
    p += [
        "<rect x='40' y='92' width='36' height='6' rx='2' fill='#34465c'/>",
        "<rect x='36' y='60' width='6' height='38' rx='2' fill='#34465c'/>",
        "<rect x='56' y='98' width='5' height='30' fill='#2a394c'/>",
    ]
    if st == "blocked":  # standing beside the desk, a red sign above
        p += [
            f"<circle cx='88' cy='42' r='10' fill='{head}'/>",
            f"<rect x='80' y='54' width='17' height='40' rx='7' fill='{body}'/>",
            f"<rect x='81' y='92' width='6' height='36' rx='3' fill='{body}'/>",
            f"<rect x='90' y='92' width='6' height='36' rx='3' fill='{body}'/>",
            f"<path d='M96 62l14 14' stroke='{body}' stroke-width='5' stroke-linecap='round'/>",
            "<circle cx='88' cy='15' r='11' fill='#e2655b'/>",
            f"<rect x='86.5' y='8' width='3' height='9' rx='1.5' fill='{ink}'/>",
            f"<circle cx='88' cy='21' r='1.8' fill='{ink}'/>",
        ]
    else:  # seated; leaning back when not working
        lean = 0 if st == "working" else -6
        hx, hy = 62 + lean, 46
        p += [
            f"<circle cx='{hx}' cy='{hy}' r='10' fill='{head}'/>",
            f"<rect x='{52 + lean}' y='58' width='18' height='36' rx='7' fill='{body}'/>",
            f"<rect x='56' y='86' width='36' height='8' rx='4' fill='{body}'/>",
            f"<rect x='84' y='88' width='7' height='40' rx='3' fill='{body}'/>",
        ]
        if st == "working":
            p.append(
                f"<path d='M66 66l46 18' stroke='{body}' stroke-width='5' stroke-linecap='round'/>"
            )
        else:  # arms folded
            p.append(
                f"<path d='M{54 + lean} 70h16' stroke='{head}' stroke-width='5' "
                "stroke-linecap='round'/>"
            )
        if st == "waiting":  # an hourglass and the reset time
            p += [
                "<rect x='76' y='8' width='76' height='24' rx='12' fill='#3a2f17' "
                "stroke='#e0a645' stroke-width='1.5'/>",
                "<path d='M84 13h10l-10 14h10z' fill='none' stroke='#e0a645' stroke-width='2' "
                "stroke-linejoin='round'/>",
                f"<text x='100' y='25' fill='#f2d49a' font-size='12' "
                f"font-family='system-ui,sans-serif'>{_e(_hm(a['reset']))}</text>",
            ]
        if st == "idle":  # a mug on the desk
            p.append("<rect x='100' y='78' width='9' height='10' rx='2' fill='#7b8da3'/>")
    p.append("</svg>")
    return "".join(p)


def _summary(d: dict) -> str:
    r = d["run"]
    pct = d["spent"] / d["budget"] * 100 if d["budget"] else 0
    cls = {"running": "working", "waiting for a limit reset": "waiting"}.get(r["state"], "idle")
    reason = r["reason"] or ("since " + _hm(d["epoch"] - (r["secs"] or 0)))
    return (
        "<section class='summary' aria-label='Summary'>"
        f"<div class='{cls}'><div class='k'>Run</div><div class='v'><span class='dot'></span>"
        f"{_e(r['state'])}</div><div class='s'>{_e(reason)}</div>{_beat(d)}</div>"
        f"<div><div class='k'>Tasks done today</div><div class='v'>{len(d['done_today'])}</div>"
        f"<div class='s'>{_e(', '.join(d['done_today']) or 'none yet')}</div></div>"
        f"<div><div class='k'>API spend, 24 h</div><div class='v'>${d['spent']:.2f}</div>"
        f"<div class='s'>of ${d['budget']:.2f} per day ({pct:.0f} %)</div>"
        f"<div class='meter'><i class='{'hot' if pct > 80 else ''}' "
        f"style='width:{min(pct, 100):.0f}%'></i></div></div>"
        f"{_kimi(d)}<div><div class='k'>Subscription tokens today</div><div class='v'>"
        f"{_tok(d['sub_tokens'])}</div><div class='s'>developer rounds</div></div>"
        f"<div><div class='k'>{'Running for' if d['active'] else 'Last run lasted'}</div>"
        f"<div class='v'>{_dur(r['secs'])}</div><div class='s'>{_e(d['now'][11:16])} now</div>"
        "</div></section>"
    )


def _kimi(d: dict) -> str:
    """Kimi's own caps (owner 2026-10-28): per 24 h and per calendar month."""
    day, month, p = d["kimi"]
    if not p:
        return ""
    pct = max(day / p["daily_usd"], month / p["monthly_usd"]) * 100
    return (
        f"<div><div class='k'>Kimi API, 24 h</div><div class='v'>${day:.2f}</div>"
        f"<div class='s'>of ${p['daily_usd']:.2f} per day; ${month:.2f} of "
        f"${p['monthly_usd']:.2f} this month{'' if p.get('enabled') else ' (off)'}</div>"
        f"<div class='meter'><i class='{'hot' if pct > 80 else ''}' "
        f"style='width:{min(pct, 100):.0f}%'></i></div></div>"
    )


def _beat(d: dict) -> str:
    if d["heartbeat"] is None:
        return ""
    stale = d["hb_stale"]
    return (
        f"<div class='s{' bad' if stale else ''}'>last heartbeat {_ago(d['heartbeat'])} ago"
        + (" — no sign of life" if stale else "")
        + "</div>"
    )


def _attention(d: dict) -> str:
    if not d["attention"]:
        return "<p class='calm'>Nothing needs you right now.</p>"
    return (
        "<ul class='att'>"
        + "".join(
            f"<li><span class='kind'>{_e(a['kind'])}</span>"
            f"<span class='do'>{_code(a['do'])}</span>"
            f"<span class='what' style='grid-column:2'>{_link(a['url'], a['text'])}</span></li>"
            for a in d["attention"]
        )
        + "</ul>"
    )


def _cards(d: dict) -> str:
    out = ["<div class='cards'>"]
    for name, a in d["office"].items():
        st = a["state"]
        task = f" on <b>{_e(a['task'])}</b>" if a["task"] else ""
        when = (
            f" · <span data-reset='{a['reset']:.0f}'>resets {_hm(a['reset'])}</span>"
            if a["reset"]
            else (f" · {_dur(a['secs'])}" if a["secs"] is not None else "")
        )
        out.append(
            f"<article class='card {st}'><div class='name'>{_e(name)}</div>"
            f"<div class='tool'>{_e(a['tool'])} · {_tok(a['tokens'])} tokens today</div>"
            f"<div><span class='dot'></span>{_e(STATE_WORD[st])}{task}{when}</div>"
            f"<div class='msg'>{_e(a['msg'][:300]) or NO_MSG}"
            "</div></article>"
        )
    out.append("</div>")
    return "".join(out)


def _queue(d: dict) -> str:
    tasks, by_id = d["tasks"], {t["id"]: t for t in d["tasks"]}

    def item(t: dict, why: str = "") -> str:
        pr = t.get("pr") or ""
        link = " " + _link(pr, "PR #" + pr.rsplit("/", 1)[-1]) if pr else ""
        return (
            f"<div class='task'><b>{_e(t['id'])}</b> {_e(t['title'])}{link}"
            + (f"<div class='why'>{_e(why)}</div>" if why else "")
            + "</div>"
        )

    def waits(t):
        left = [x for x in t.get("depends_on") or [] if by_id.get(x, {}).get("status") != "done"]
        return f"waits for {', '.join(left)}" if left else ""

    cols = [
        (
            "In progress",
            [item(t, t.get("owner", "")) for t in tasks if t["status"] == "in_progress"],
        ),
        ("Todo, in order", [item(t, waits(t)) for t in tasks if t["status"] == "todo"]),
        ("Later", [item(t, t.get("blocked_reason", "")) for t in tasks if t["status"] == "later"]),
        (
            "Blocked",
            [item(t, t.get("blocked_reason", "")) for t in tasks if t["status"] == "blocked"],
        ),
        ("Done today", [item(by_id[i]) for i in d["done_today"]]),
    ]
    older = [t for t in tasks if t["status"] == "done" and t["id"] not in d["done_today"]]
    out = ["<div class='cols'>"]
    for title, items in cols:
        out.append(
            f"<section class='col'><h3>{_e(title)} <span class='faint'>{len(items)}</span></h3>"
            + ("".join(items) or "<div class='task faint'>None.</div>")
        )
        if title == "Done today" and older:
            out.append(
                f"<details><summary class='faint'>{len(older)} done earlier</summary>"
                + "".join(item(t) for t in older)
                + "</details>"
            )
        out.append("</section>")
    out.append("</div>")
    return "".join(out)


def _timeline(d: dict) -> str:
    tl = d["timeline"]
    t0, t1 = tl["start"], tl["end"]
    span = max(t1 - t0, 60)
    out = ["<div class='lanes'>"]
    for agent, segs in tl["rows"].items():
        bars = []
        for s in segs:
            left = (s["start"] - t0) / span * 100
            width = max((s["end"] - s["start"]) / span * 100, 0.3)
            tip = f"{agent}: {s['task']} {STATE_WORD[s['state']]} {_hm(s['start'])}–" + (
                "now" if s.get("open") else _hm(s["end"])
            )
            bars.append(
                f"<i class='seg {s['state']}' style='left:{left:.2f}%;width:{width:.2f}%' "
                f"title='{_e(tip)}'></i>"
            )
        out.append(
            f"<div class='lane'><span>{_e(agent)}</span>"
            f"<div class='track'>{''.join(bars)}</div></div>"
        )
    hour = 3600
    first = (int(t0) // hour + 1) * hour
    step = hour * max(1, int(span // hour // 8) or 1)
    ticks = "".join(
        f"<span style='left:{(t - t0) / span * 100:.2f}%'>{_hm(t)}</span>"
        for t in range(first, int(t1), step)
    )
    out.append(f"<div class='ticks'>{ticks}</div>")
    out.append(
        "<div class='legend'><span><span class='dot working'></span>working</span>"
        "<span><span class='dot waiting'></span>waiting for a limit reset</span>"
        "<span class='faint'>Hover a bar for the task and times.</span></div></div>"
    )
    return "".join(out)


def _bars(d: dict) -> str:
    rows, cap = d["task_tokens"], d["task_limit"]
    if not rows:
        return "<p class='calm'>No developer rounds today.</p>"
    top = max([n for _t, n in rows] + [cap or 0])
    out = ["<div class='bars'>"]
    for t, n in rows:
        mark = f"<span class='cap' style='left:{cap / top * 100:.2f}%'></span>" if cap else ""
        out.append(
            f"<div class='brow' title='{_e(t)}: {n:,} subscription tokens today'>"
            f"<b>{_e(t)}</b><div class='btrack'><i style='width:{n / top * 100:.2f}%'></i>"
            f"{mark}</div><span class='num'>{_tok(n)}</span></div>"
        )
    if cap:
        out.append(
            f"<div class='legend'><span>Dashed line: the per-task limit ({_tok(cap)}, "
            "config supervisor). A task stops when it reaches it.</span></div>"
        )
    out.append("</div>")
    return "".join(out)


def _limits(d: dict) -> str:
    rows = d["limits"]
    if not rows:
        return "<p class='calm'>No subscription limit was hit in the last 7 days.</p>"
    body = "".join(
        f"<tr><td class='nowrap'>{_e(_when(ts, d['now']))}</td><td>{_e(a)}</td><td>{_e(t)}</td><td>"
        + (
            f"{_e(_hm(r))} · <span data-reset='{r:.0f}'></span>"
            if r and r > d["epoch"]
            else (_e(_hm(r)) if r else "unknown")
        )
        + f"</td><td>{(w or 0) / 60:.0f} min</td><td class='muted'>{_e(msg)}</td></tr>"
        for ts, a, t, r, w, msg in rows
    )
    return (
        "<div class='box'><table><tr><th>When</th><th>Developer</th><th>Task</th><th>Resets</th>"
        f"<th>Waited</th><th>Message</th></tr>{body}</table></div>"
    )


def _violations(d: dict) -> str:
    rows = d["violations"]
    if not rows:
        return "<p class='calm'>No scope or bypass violation today.</p>"
    body = "".join(
        f"<tr><td>{_e(v['ts'][11:16])}</td><td>{_e(v['task'])}</td><td>{_e(v['agent'])}</td>"
        f"<td><span class='dot blocked'></span>{_e(v['kind'].replace('_', ' '))}</td>"
        f"<td class='muted'>{_e(v['detail'][:400])}</td></tr>"
        for v in rows
    )
    return (
        "<div class='box'><table><tr><th>When</th><th>Task</th><th>Developer</th><th>Kind</th>"
        f"<th>What</th></tr>{body}</table></div>"
    )


def _metrics(d: dict) -> str:
    bold = re.compile(r"\*\*(.+?)\*\*")
    items = "".join(f"<li>{bold.sub(r'<b>\1</b>', _e(x))}</li>" for x in metric_lines(d["metrics"]))
    return f"<div class='box'><ul>{items}</ul></div>"


def _costs(d: dict) -> str:
    rows = (
        "".join(
            f"<tr><td>{_e(a)}</td><td>{_e(prov or '')}</td><td>{_e(m)}</td><td>{n}</td>"
            + (
                f"<td>${usd:.3f}"
                + (f" <span class='muted'>({_e(costs.stale_note(stale))})</span>" if stale else "")
                + "</td>"
                if billed
                else f"<td class='muted'>≈ ${usd:.2f}, not billed</td>"
            )
            + f"<td>{'API' if billed else 'subscription'}</td>"
            + (
                f"<td>{_e(costs.cache_hits(ti, cr))}</td><td>{cw or 0:,} tok</td>"
                if billed
                else "<td class='faint'>—</td><td class='faint'>—</td>"
            )
            + "</tr>"
            for a, m, n, usd, billed, ti, cr, cw, stale, prov in d["cost_rows"]
        )
        or "<tr><td colspan='8' class='muted'>No calls today.</td></tr>"
    )
    return (
        "<div class='box'><table><tr><th>Agent</th><th>Provider</th><th>Model</th><th>Calls</th>"
        "<th>$</th>"
        "<th>Billing</th><th>Prompt cache hits</th><th>Cache writes</th></tr>"
        f"{rows}</table></div>"
    )


def _github(d: dict) -> str:
    if isinstance(d["prs"], dict):
        prs = f"<p class='muted'>gh: {_e(d['prs'].get('error'))}</p>"
    else:
        prs = (
            "<table>"
            + (
                "".join(
                    f"<tr><td>{_link(x['url'], '#' + str(x['number']))}</td>"
                    f"<td>{_e(x['title'])}</td>"
                    f"<td class='mono'>{_e(x['headRefName'])} → {_e(x['baseRefName'])}</td></tr>"
                    for x in d["prs"]
                )
                or "<tr><td class='muted'>None.</td></tr>"
            )
            + "</table>"
        )
    if isinstance(d["ci"], dict) and "error" in d["ci"]:
        ci = f"<p class='muted'>gh: {_e(d['ci']['error'])}</p>"
    else:
        ci = (
            "<table>"
            + (
                "".join(
                    f"<tr><td>{_e(name)}</td><td><span class='dot "
                    f"{'working' if r.get('conclusion') == 'success' else 'blocked'}'></span>"
                    f"{_e(r.get('conclusion'))}</td><td class='mono'>{_e(r['headSha'][:8])}</td>"
                    f"<td>{_link(r['url'], 'run')}</td></tr>"
                    for name, r in d["ci"].items()
                )
                or "<tr><td class='muted'>No completed runs.</td></tr>"
            )
            + "</table>"
        )
    log = "".join(f"<div>{_e(line)}</div>" for line in d["log"])
    return (
        "<div class='two'><div><h2>Open pull requests</h2><div class='box'>" + prs + "</div>"
        "<h2>CI on dev</h2><div class='box'>" + ci + "</div></div>"
        "<div><h2>Latest commits on dev</h2><div class='box mono'>" + log + "</div></div></div>"
    )


def _office(d: dict) -> str:
    rooms, panels = [], []
    for name, a in d["office"].items():
        st = a["state"]
        line = STATE_WORD[st] + (f" · {a['task']}" if a["task"] else "")
        rooms.append(
            f"<button class='room' data-who='{_e(name)}' aria-pressed='false' "
            f"aria-label='{_e(a['room'])}: {_e(name)}, {_e(line)}'>"
            f"<div class='wall'>{figure(a)}</div><div class='plate {st}'>"
            f"<div class='dept'>{_e(a['room'])}</div>"
            f"<div class='who'>{_e(name)} · {_e(a['role'])}</div>"
            f"<div><span class='dot'></span>{_e(line)}</div></div></button>"
        )
        last = (
            "".join(
                f"<li><span class='faint'>{_e(e['t'][:5])}</span> {_e(e['task'])} "
                f"{_e(e['msg'][:240])}</li>"
                for e in reversed(a["last"])
            )
            or "<li>No messages today.</li>"
        )
        when = (
            f"{_hm(a['reset'])} (<span data-reset='{a['reset']:.0f}'></span>)"
            if a["reset"]
            else "—"
        )
        panels.append(
            f"<section class='who-panel' data-who='{_e(name)}' hidden aria-live='polite'>"
            f"<b>{_e(a['room'])}: {_e(name)}</b> <span class='muted'>{_e(a['tool'])}</span><dl>"
            f"<dt>State</dt><dd class='{st}'><span class='dot'></span>{_e(STATE_WORD[st])}</dd>"
            f"<dt>Task</dt><dd>{_e(a['task'] or '—')}</dd>"
            f"<dt>Time on it</dt><dd>{_dur(a['secs'])}</dd>"
            f"<dt>Limit resets</dt><dd>{when}</dd>"
            f"<dt>Tokens today</dt><dd>{a['tokens']:,}</dd></dl>"
            f"<div class='muted'>Last messages</div><ul class='log'>{last}</ul></section>"
        )
    return (
        "<p class='muted'>One room per department. Click a person for their task, time and last "
        "messages.</p><div class='rooms'>" + "".join(rooms) + "</div>" + "".join(panels)
    )


def render(d: dict) -> str:
    return "".join(
        [
            "<!doctype html><html lang='en'><head><meta charset='utf-8'>",
            "<meta name='viewport' content='width=device-width,initial-scale=1'>",
            "<noscript><meta http-equiv='refresh' content='20'></noscript>",
            f"<title>Kerege dashboard</title><style>{CSS}</style></head><body><main>",
            "<header><h1>Kerege</h1><span class='muted'>"
            + _e(d["now"])
            + " · read-only · refreshes every 20 s</span><nav aria-label='Views'>"
            "<button data-go='overview' aria-selected='true'>Overview</button>"
            "<button data-go='office' aria-selected='false'>Office</button></nav></header>",
            _summary(d),
            "<div data-tab='overview'>",
            "<h2>Needs your attention</h2>",
            _attention(d),
            "<h2>Agents</h2>",
            _cards(d),
            "<h2>Queue</h2>",
            _queue(d),
            f"<h2>Metrics, last {d['metrics']['days']} days</h2>",
            _metrics(d),
            "<h2>Today</h2>",
            _timeline(d),
            "<div class='two'><div><h2>Subscription tokens per task today</h2>",
            _bars(d),
            "</div><div><h2>Subscription limits, last 7 days</h2>",
            _limits(d),
            "</div></div><h2>Scope and bypass violations today</h2>",
            _violations(d),
            "<h2>API spend today</h2>",
            _costs(d),
            _github(d),
            "</div><div data-tab='office' hidden>",
            _office(d),
            f"</div></main><script>{JS}</script></body></html>",
        ]
    )


def page(team: Team, gh: GhCache) -> str:
    return team.redact(render(collect(team, gh)))


class Server(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        if isinstance(sys.exc_info()[1], ConnectionError):  # reset / broken pipe: the browser
            return  # closed the tab or reloaded mid-response, nothing went wrong here
        super().handle_error(request, client_address)


def make_server(team: Team, port: int, gh: GhCache | None = None) -> ThreadingHTTPServer:
    gh = gh or GhCache(team)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # the only method: read-only
            if self.path.split("?")[0] != "/":
                self.send_error(404)
                return
            body = page(team, gh).encode("utf-8", "replace")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # quiet terminal
            pass

    return Server(("127.0.0.1", port), Handler)  # local only


def serve(team: Team, port: int):
    server = make_server(team, port)
    print(f"Kerege dashboard: http://127.0.0.1:{server.server_port}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
