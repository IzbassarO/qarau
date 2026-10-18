"""Daily report for the owner: devteam/reports/YYYY-MM-DD.md (rewritten on every run)."""

import json
import time
from pathlib import Path

from devteam import costs
from devteam.continuous import runs_of_day, summary_lines
from devteam.gates import bench_table


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
    lines += ["## Merged into dev", ""]
    lines += [f"- {t['id']} {t['title']} — {t.get('pr', '')}" for t in done] or ["- none"]
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
        f"| {a} | {m} | {n} | {usd:.3f} | {'API' if billed else 'subscription'} | "
        f"{costs.cache_hits(ti, cr) if billed else '—'} |"
        for a, m, n, usd, billed, ti, cr, _cw in rows
    ] or ["| — | — | 0 | 0 | — | — |"]
    lines += [
        "",
        f"API spend: **${sum(r[3] for r in rows if r[4]):.2f}** of "
        f"${team.cfg['daily_budget_usd']:.2f} per day",
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
    out.write_text(team.redact("\n".join(lines)))
    return out
