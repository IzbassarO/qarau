"""Spend log. `billed` rows are pay-per-use API calls and count toward the daily cap;
subscription rows (Claude Code, Codex) are logged for information only."""

import sqlite3
import time
from pathlib import Path

from rich.table import Table


class BudgetExceeded(RuntimeError):
    pass


def _db(home: Path) -> sqlite3.Connection:
    db = sqlite3.connect(home / "costs.sqlite")
    db.execute("""create table if not exists usage (
        day text, ts text, agent text, model text, task text,
        tokens_in int, tokens_out int, usd real, billed int)""")
    return db


def record(
    home: Path,
    agent: str,
    model: str,
    task: str,
    tokens_in: int,
    tokens_out: int,
    usd: float,
    billed: bool,
):
    with _db(home) as db:
        db.execute(
            "insert into usage values (?,?,?,?,?,?,?,?,?)",
            (
                time.strftime("%Y-%m-%d"),
                time.strftime("%H:%M:%S"),
                agent,
                model,
                task,
                tokens_in,
                tokens_out,
                usd,
                int(billed),
            ),
        )


def spent_today(home: Path) -> float:
    with _db(home) as db:
        row = db.execute(
            "select coalesce(sum(usd), 0) from usage where billed and day = ?",
            (time.strftime("%Y-%m-%d"),),
        ).fetchone()
    return row[0]


def check_budget(home: Path, cap: float):
    """Call before every billed request. ponytail: the call that crosses the cap still runs."""
    if (spent := spent_today(home)) >= cap:
        raise BudgetExceeded(
            f"daily API budget reached: ${spent:.2f} of ${cap:.2f}. "
            "`devteam resume` tomorrow, or raise daily_budget_usd in config.yaml"
        )


def report(home: Path, console):
    t = Table("day", "agent", "model", "calls", "tokens in", "tokens out", "$", "billed")
    with _db(home) as db:
        rows = db.execute("""select day, agent, model, count(*), sum(tokens_in), sum(tokens_out),
            sum(usd), billed from usage group by day, agent, model, billed
            order by day desc, agent""").fetchall()
    for day, agent, model, n, ti, to, usd, billed in rows:
        t.add_row(
            day,
            agent,
            model,
            str(n),
            f"{ti:,}",
            f"{to:,}",
            f"{usd:.3f}",
            "API" if billed else "subscription",
        )
    console.print(t)
    console.print(f"API spend today: ${spent_today(home):.2f}")
