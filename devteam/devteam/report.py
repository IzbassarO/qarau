"""Daily report for the owner: devteam/reports/YYYY-MM-DD.md (rewritten on every run)."""

import datetime as dt
import json
import re
import time
from collections import Counter
from pathlib import Path

import yaml

from devteam import costs
from devteam.continuous import runs_of_day, summary_lines
from devteam.gates import bench_table

# Tester verdicts in the events log (agent "tester"), by how each starts: a part accepted, the
# whole task's checks and criteria passed, or a send-back of any kind (a gate's too).
ACCEPT_PART = re.compile(r"^part \d+/\d+ accepted")
ACCEPT_LAST = "local checks and criteria pass"
SENT_BACK = ("sent back", "out of fix loops", "TESTER ERROR", LOOP := "BLOCKED: the tester sent")


def _jsonl(team, log: str, since: str) -> list[dict]:
    try:
        lines = (team.home / log).read_text().splitlines()
    except FileNotFoundError:
        return []
    return [r for r in map(json.loads, lines) if r["ts"] >= since]


def _events(team) -> list[dict]:
    try:
        text = (team.home / "events.log").read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return []
    return yaml.load(text, Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader)) or []


def metrics(team, now: float | None = None, days: int = 7) -> dict:
    """The last `days` days, from existing state only: the backlog, events.log, costs.sqlite,
    flaky.jsonl, tester_errors.jsonl. A part counts as accepted on its first round when no
    send-back came between it and the previous acceptance (or the task's start); a gate's
    send-back after the last part re-opens that part.
    ponytail: rounds come from the tester's log lines; a part with no line (a crash) is unseen."""
    now = time.time() if now is None else now
    since = time.strftime("%Y-%m-%d", time.localtime(now - (days - 1) * 86400))
    tasks = team.load_backlog()
    done = [t["id"] for t in tasks if t["status"] == "done" and t.get("done_at", "") >= since]
    rounds: dict[str, int] = {}
    parts: dict[str, list[list]] = {}  # task -> [[ts, first round], ...]
    fails: dict[str, int] = {}
    last_open: dict[str, bool] = {}  # the last acceptance was the whole task's: a gate may undo
    for e in _events(team):
        tid, msg, ts = e.get("task") or "", str(e.get("msg", "")), str(e.get("ts") or "")
        if not tid or (e.get("agent") != "tester" and not msg.startswith(LOOP)):
            continue  # the loop guard's block is said by the developer's agent
        if msg.startswith(SENT_BACK):
            rounds[tid] = rounds.get(tid, 0) + 1
            fails[tid] = fails.get(tid, 0) + 1
        elif ACCEPT_PART.match(msg) or msg == ACCEPT_LAST:
            rounds[tid] = rounds.get(tid, 0) + 1
            row = [ts, not fails.get(tid)]
            if msg == ACCEPT_LAST and last_open.get(tid) and fails.get(tid):
                parts[tid][-1] = [ts, False]  # the same last part, accepted again
            else:
                parts.setdefault(tid, []).append(row)
            fails[tid], last_open[tid] = 0, msg == ACCEPT_LAST
    accepted = [first for rows in parts.values() for ts, first in rows if ts[:10] >= since]
    usd = costs.task_usd(team.home)
    flaky = Counter(t for r in _jsonl(team, "flaky.jsonl", since) for t in r["tests"])
    mean = lambda xs: sum(xs) / len(xs) if xs else None  # noqa: E731
    return {
        "days": days,
        "since": since,
        "done": len(done),
        "first_round": mean([float(x) for x in accepted]),
        "parts": len(accepted),
        "rounds_per_task": mean([rounds.get(t, 0) for t in done]),
        "usd_per_task": mean([usd.get(t, 0.0) for t in done]),
        "blocked": sum(t["status"] == "blocked" for t in tasks),
        "tester_errors": len(_jsonl(team, "tester_errors.jsonl", since)),
        "flaky": flaky.most_common(),
    }


def metric_lines(m: dict) -> list[str]:
    """The metrics as plain lines: the daily report shows them, the dashboard too."""
    pct = "—" if m["first_round"] is None else f"{m['first_round']:.0%} of {m['parts']}"
    per = lambda x, f: "—" if x is None else f.format(x)  # noqa: E731
    return [
        f"Tasks done: **{m['done']}**",
        f"Parts accepted on the first round: **{pct}**",
        f"Mean rounds per done task: **{per(m['rounds_per_task'], '{:.1f}')}**",
        f"API $ per done task: **{per(m['usd_per_task'], '${:.2f}')}**",
        f"Blocked now: **{m['blocked']}**",
        f"Tester errors (incomplete verdicts): **{m['tester_errors']}**",
        "Flaky tests: "
        + (", ".join(f"{name} ×{n}" for name, n in m["flaky"]) if m["flaky"] else "none"),
    ]


def write_report(team, day: str | None = None) -> Path:
    day = day or time.strftime("%Y-%m-%d")
    tasks = team.load_backlog()
    done = [t for t in tasks if t["status"] == "done" and t.get("done_at") == day]
    blocked = [t for t in tasks if t["status"] == "blocked"]
    open_ = [t for t in tasks if t["status"] in ("todo", "in_progress")]

    lines = [
        f"# Kerege report — {day}",
        "",
        f"Done today: **{len(done)}** · blocked: **{len(blocked)}** · open: {len(open_)}",
        "",
    ]
    m = metrics(team, time.mktime(time.strptime(day, "%Y-%m-%d")) + 86399)
    lines += [f"## Metrics (last {m['days']} days, since {m['since']})", ""]
    lines += [f"- {x}" for x in metric_lines(m)] + [""]
    lines += ["## Merged into dev", ""]
    lines += [f"- {t['id']} {t['title']} — {t.get('pr', '')}" for t in done] or ["- none"]
    lines += ["", "## Scope and bypass violations", ""]
    lines += [
        f"- {v['ts'][11:16]} {v['task']} ({v['agent']}) **{v['kind']}**: {v['detail'][:300]}"
        for v in team.violations(day)
    ] or ["- none"]
    lines += ["", "## Flaky e2e tests (failed, then passed on retry: not blocking)", ""]
    lines += [
        f"- {r['ts'][11:16]} {r['task']}: {t}"
        for r in team.violations(day, "flaky.jsonl")
        for t in r["tests"]
    ] or ["- none"]
    lines += ["", "## Blocked (need attention)", ""]
    lines += [
        f"- {t['id']} {t['title']}: {t.get('blocked_reason', '')[:300]}" for t in blocked
    ] or ["- none"]

    lines += [
        "",
        "## Cost",
        "",
        "| Agent | Model | Calls | $ | Billing | Cache hits |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    rows = costs.day_rows(team.home, day)
    lines += [
        f"| {a} | {m} | {n} | {usd:.3f} | {'API' if billed else 'subscription'}"
        f"{f' ({costs.stale_note(stale)})' if stale else ''} | "
        f"{costs.cache_hits(ti, cr) if billed else '—'} |"
        for a, m, n, usd, billed, ti, cr, _cw, stale in rows
    ] or ["| — | — | 0 | 0 | — | — |"]
    lines += [
        "",
        f"API spend this day: **${sum(r[3] for r in rows if r[4]):.2f}**; the cap is "
        f"${team.cfg['daily_budget_usd']:.2f} over any 24 h",
    ]

    lines += [
        "",
        "## Subscription load per task",
        "",
        "Developer runs on your Claude / ChatGPT plans (not billed per call). Minutes and tokens",
        "show how fast the plan limits go; ≈ API $ is what Claude Code says the same work would",
        "cost on the API (Codex reports tokens only).",
        "",
        "| Task | Developer | Tool | Rounds | Minutes | Tokens in | Tokens out | ≈ API $ |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    sub = costs.subscription_rows(team.home, day)
    lines += [
        f"| {t} | {a} | {m} | {n} | {secs / 60:.0f} | {ti:,} | {to:,} | "
        + (f"{usd:.2f}" if usd else "—")
        + " |"
        for t, a, m, n, secs, ti, to, usd in sub
    ] or ["| — | — | — | 0 | 0 | 0 | 0 | — |"]
    for m in sorted({r[2] for r in sub}):
        mine = [r for r in sub if r[2] == m]
        lines.append(
            f"| **total** | | {m} | {sum(r[3] for r in mine)} | "
            f"{sum(r[4] for r in mine) / 60:.0f} | {sum(r[5] for r in mine):,} | "
            f"{sum(r[6] for r in mine):,} | "
            + (f"{t:.2f}" if (t := sum(r[7] for r in mine)) else "—")
            + " |"
        )

    runs = runs_of_day(team.home, day)
    if runs:
        lines += ["", "## Continuous runs", ""]
        for r in runs:
            lines += summary_lines(r)
    limits = [r for r in costs.limit_rows(team.home, day) if r[0].startswith(day)]
    if limits:
        lines += ["", "## Subscription limits", "", "| Time | Developer | Task | Reset | Waited |"]
        lines += ["| --- | --- | --- | --- | --- |"]
        lines += [
            f"| {ts[11:16]} | {a} | {t or '—'} | "
            + (time.strftime("%H:%M", time.localtime(reset)) if reset else "unknown")
            + f" | {waited / 60:.0f} min |"
            for ts, a, t, reset, waited, _msg in limits
        ]

    lines += ["", "## Benchmark (latest on dev)", ""]
    bench = sorted((team.home / "bench").glob("*/results.json"), key=lambda p: p.stat().st_mtime)
    if bench:
        res = json.loads(bench[-1].read_text())
        lines += [
            f"Commit `{res.get('commit', '?')}`",
            "",
            bench_table(res),
        ]
    else:
        lines += ["Not available yet (the benchmark command comes with task P1)."]
    last = team.home / "last_release"
    lines += [
        "",
        "## Release dev → main",
        "",
        f"Last release: {last.read_text() if last.exists() else 'none yet'}",
        "",
    ]

    out = (team.home if team.fake else team.root / "devteam") / "reports" / f"{day}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(team.redact("\n".join(lines)), encoding="utf-8", errors="replace")
    return out


def write_reports(team, since: float) -> Path:
    """The report of every calendar day from `since` (the run's start, epoch) to today, so a
    run past midnight is in both days. Returns today's."""
    day, today = dt.date.fromtimestamp(since), dt.date.today()
    while day < today:
        write_report(team, day.isoformat())
        day += dt.timedelta(days=1)
    return write_report(team, today.isoformat())
