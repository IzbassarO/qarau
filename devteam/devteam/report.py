"""Daily report for the owner: devteam/reports/YYYY-MM-DD.md (rewritten on every run)."""

import json
import time
from pathlib import Path

from devteam import costs
from devteam.gates import bench_table


def write_report(team, day: str | None = None) -> Path:
    day = day or time.strftime("%Y-%m-%d")
    tasks = team.load_backlog()
    done = [t for t in tasks if t["status"] == "done" and t.get("done_at") == day]
    blocked = [t for t in tasks if t["status"] == "blocked"]
    open_ = [t for t in tasks if t["status"] in ("todo", "in_progress")]

    lines = [
        f"# Dev team report — {day}",
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
        "| Agent | Model | Calls | $ | Billing |",
        "| --- | --- | --- | --- | --- |",
    ]
    rows = costs.day_rows(team.home, day)
    lines += [
        f"| {a} | {m} | {n} | {usd:.3f} | {'API' if billed else 'subscription'} |"
        for a, m, n, usd, billed in rows
    ] or ["| — | — | 0 | 0 | — |"]
    lines += [
        "",
        f"API spend: **${sum(r[3] for r in rows if r[4]):.2f}** of "
        f"${team.cfg['daily_budget_usd']:.2f} per day",
    ]

    lines += ["", "## Benchmark (latest on dev)", ""]
    bench = sorted((team.home / "bench").glob("*/results.json"), key=lambda p: p.stat().st_mtime)
    if bench:
        lines += [
            f"Commit `{bench[-1].parent.name[:8]}`",
            "",
            bench_table(json.loads(bench[-1].read_text())),
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
