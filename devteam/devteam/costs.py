"""Spend log and the API budget: daily_budget_usd over any rolling 24 h, so a run past
midnight cannot spend the cap twice in one night.

Every billed call first *reserves* its worst-case cost in one SQLite transaction
(BEGIN IMMEDIATE = one writer at a time, across threads and processes), then the
reservation is settled to the real cost. A reservation that is never settled (crash,
network error with unknown billing) keeps counting at its worst case; after STALE_H it is
marked `stale` (owner, 2026-10-21: billing unknown, still counted at max, no longer open).
Subscription runs (Claude Code, Codex) are logged with billed=0, for information only.
"""

import datetime as dt
import re
import sqlite3
import time
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from rich.table import Table

STALE_H = 2  # no call stays open this long: 4 tries, 255 s of waits between them, the stream


class BudgetExceeded(RuntimeError):
    pass


class SubscriptionLimit(RuntimeError):
    """A Claude Code / Codex plan limit. reset_at: epoch seconds, None if the tool didn't say."""

    def __init__(self, message: str, reset_at: float | None):
        super().__init__(message)
        self.reset_at = reset_at


LIMIT_HIT = re.compile(
    r"usage limit|limit reached|hit your (?:usage )?limit|rate.?limit|quota exceeded", re.I
)


def limit_reset(text: str, now: float | None = None) -> tuple[bool, float | None]:
    """(is a plan limit, reset epoch or None) from what claude / codex print. Seen forms:
    "Claude AI usage limit reached|1760000000", "5-hour limit reached ∙ resets 3pm",
    "You've hit your limit · resets 3:30pm (America/New_York)",
    "try again in 2 hours 5 minutes", "try again at 3:05 PM"."""
    if not LIMIT_HIT.search(text):
        return False, None
    now = time.time() if now is None else now
    if m := re.search(r"\|(\d{10})\b", text):
        return True, float(m.group(1))
    if m := re.search(r"try again in\s+(?:(\d+)\s*h\w*)?\s*(?:(\d+)\s*m\w*)?", text, re.I):
        if m.group(1) or m.group(2):
            return True, now + int(m.group(1) or 0) * 3600 + int(m.group(2) or 0) * 60
    m = re.search(
        r"(?:resets?|try again)\s+(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*([ap]\.?m\.?)?"
        r"(?:\s*\(([\w/+-]+)\))?",
        text,
        re.I,
    )
    if not m:
        return True, None
    hour, minute = int(m.group(1)), int(m.group(2) or 0)
    if m.group(3):
        hour = hour % 12 + (12 if m.group(3).lower().startswith("p") else 0)
    if hour > 23 or minute > 59:
        return True, None
    try:
        tz = ZoneInfo(m.group(4)) if m.group(4) else None
    except (ZoneInfoNotFoundError, ValueError):
        tz = None
    t0 = dt.datetime.fromtimestamp(now, tz)
    reset = t0.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if reset <= t0:
        reset += dt.timedelta(days=1)
    return True, reset.timestamp()


def _db(home: Path) -> sqlite3.Connection:
    db = sqlite3.connect(home / "costs.sqlite", timeout=30, isolation_level=None)
    db.execute("""create table if not exists usage (
        day text, ts text, agent text, model text, task text,
        tokens_in int, tokens_out int, usd real, billed int, state text default 'settled',
        secs real default 0, cache_read int default 0, cache_write int default 0)""")
    cols = {row[1] for row in db.execute("pragma table_info(usage)")}
    for col, decl in (  # older dbs; tokens_in includes cache reads and writes
        ("state", "text default 'settled'"),
        ("secs", "real default 0"),
        ("cache_read", "int default 0"),
        ("cache_write", "int default 0"),
    ):
        if col not in cols:
            try:
                db.execute(f"alter table usage add column {col} {decl}")
            except sqlite3.OperationalError as e:  # another thread or process added it first
                if "duplicate column" not in str(e):
                    raise
    db.execute("""create table if not exists limits (
        day text, ts text, agent text, task text, reset_at real, waited_s real, message text)""")
    return db


def _today() -> str:
    return time.strftime("%Y-%m-%d")


def _since_24h(now: float | None = None) -> str:
    """Local time 24 h ago, comparable with day || 'T' || ts.
    ponytail: local wall clock, so a DST change shifts the window by an hour once."""
    t = (time.time() if now is None else now) - 24 * 3600
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t))


def _spent(db, since: str) -> float:
    return db.execute(
        "select coalesce(sum(usd), 0) from usage where billed and day || 'T' || ts >= ?",
        (since,),
    ).fetchone()[0]


def reserve(home: Path, cap: float, agent: str, model: str, task: str, max_usd: float) -> int:
    """Atomically check the cap and hold max_usd. Returns the reservation id."""
    db = _db(home)
    try:
        db.execute("begin immediate")
        spent = _spent(db, _since_24h())
        if spent + max_usd > cap:
            db.execute("rollback")
            raise BudgetExceeded(
                f"API budget: ${spent:.2f} spent or reserved in the last 24 h of ${cap:.2f}, this "
                f"call may cost up to ${max_usd:.2f}. `devteam resume` once older calls leave the "
                "24 h window, or raise daily_budget_usd in config.yaml"
            )
        cur = db.execute(
            "insert into usage (day, ts, agent, model, task, tokens_in, tokens_out, usd, billed, "
            "state) values (?,?,?,?,?,0,0,?,1,'reserved')",
            (_today(), time.strftime("%H:%M:%S"), agent, model, task, max_usd),
        )
        db.execute("commit")
        return cur.lastrowid
    finally:
        db.close()


def mark_stale(home: Path, now: float | None = None) -> int:
    """Reservations still open after STALE_H: their call was cut (crash, Ctrl+C) and what the
    API billed is unknown. They become `stale`, kept at the worst case (money is never
    under-counted), so they no longer show as calls in flight. Returns how many."""
    t = (time.time() if now is None else now) - STALE_H * 3600
    cutoff = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t))
    with _db(home) as db:
        return db.execute(
            "update usage set state='stale' where state='reserved' and day || 'T' || ts < ?",
            (cutoff,),
        ).rowcount


def settle(
    home: Path,
    rid: int,
    tokens_in: int,
    tokens_out: int,
    usd: float,
    cache_read: int = 0,
    cache_write: int = 0,
):
    """tokens_in: all input, cache reads and writes included."""
    with _db(home) as db:
        db.execute(
            "update usage set tokens_in=?, tokens_out=?, usd=?, cache_read=?, cache_write=?, "
            "state='settled' where rowid=?",
            (tokens_in, tokens_out, usd, cache_read, cache_write, rid),
        )


def release(home: Path, rid: int):
    """Only when the API surely did not bill (it answered with an error status)."""
    with _db(home) as db:
        db.execute("delete from usage where rowid=? and state='reserved'", (rid,))


def record(
    home: Path,
    agent: str,
    model: str,
    task: str,
    tokens_in: int,
    tokens_out: int,
    usd: float,
    billed: bool,
    secs: float = 0,
):
    with _db(home) as db:
        db.execute(
            "insert into usage (day, ts, agent, model, task, tokens_in, tokens_out, usd, billed, "
            "state, secs) values (?,?,?,?,?,?,?,?,?,'settled',?)",
            (
                _today(),
                time.strftime("%H:%M:%S"),
                agent,
                model,
                task,
                tokens_in,
                tokens_out,
                usd,
                int(billed),
                secs,
            ),
        )


def spent_24h(home: Path, now: float | None = None) -> float:
    """What the budget counts: billed spend and open reservations of the last 24 h."""
    db = _db(home)
    try:
        return _spent(db, _since_24h(now))
    finally:
        db.close()


def report(home: Path, console):
    t = Table("day", "agent", "model", "calls", "tokens in", "tokens out", "$", "billed")
    db = _db(home)
    rows = db.execute("""select day, agent, model, count(*), sum(tokens_in), sum(tokens_out),
        sum(usd), billed, sum(state = 'reserved'), sum(state = 'stale') from usage
        group by day, agent, model, billed order by day desc, agent""").fetchall()
    db.close()
    for day, agent, model, n, ti, to, usd, billed, open_, stale in rows:
        kind = "API" if billed else "subscription"
        if open_:
            kind += f" ({open_} unsettled, counted at max)"
        if stale:
            kind += f" ({stale_note(stale)})"
        t.add_row(day, agent, model, str(n), f"{ti:,}", f"{to:,}", f"{usd:.3f}", kind)
    console.print(t)
    console.print(f"API spend, last 24 h (incl. reservations): ${spent_24h(home):.2f}")


def stale_note(n: int) -> str:
    return f"{n} stale: billing unknown, counted at max"


def day_rows(home: Path, day: str) -> list[tuple]:
    """(agent, model, calls, usd, billed, tokens_in, cache_read, cache_write, stale) per
    agent/model."""
    db = _db(home)
    try:
        return db.execute(
            """select agent, model, count(*), sum(usd), billed, sum(tokens_in),
            sum(cache_read), sum(cache_write), sum(state = 'stale') from usage
            where day = ? group by agent, model, billed order by agent""",
            (day,),
        ).fetchall()
    finally:
        db.close()


def agent_tokens(home: Path, day: str) -> dict[str, int]:
    """Tokens in + out per agent today (API and subscription)."""
    db = _db(home)
    try:
        rows = db.execute(
            "select agent, sum(tokens_in) + sum(tokens_out) from usage where day = ? "
            "group by agent",
            (day,),
        ).fetchall()
    finally:
        db.close()
    return {a: n or 0 for a, n in rows}


def cache_hits(tokens_in, cache_read) -> str:
    """Share of the input tokens read from the prompt cache: '62 % (81,000 tok)'."""
    if not tokens_in:
        return "—"
    return f"{(cache_read or 0) / tokens_in * 100:.0f} % ({cache_read or 0:,} tok)"


def subscription_rows(home: Path, day: str) -> list[tuple]:
    """Developer runs on subscriptions, per task: (task, agent, model, runs, secs, in, out, usd)."""
    db = _db(home)
    try:
        return db.execute(
            """select task, agent, model, count(*), sum(secs), sum(tokens_in), sum(tokens_out),
            sum(usd) from usage where day = ? and not billed
            group by task, agent, model order by sum(secs) desc""",
            (day,),
        ).fetchall()
    finally:
        db.close()


def record_limit(
    home: Path, agent: str, task: str, reset_at: float | None, waited_s: float, message: str
):
    """A subscription-limit event (shown in the dashboard and the report)."""
    with _db(home) as db:
        db.execute(
            "insert into limits values (?,?,?,?,?,?,?)",
            (
                _today(),
                time.strftime("%Y-%m-%dT%H:%M:%S"),
                agent,
                task,
                reset_at,
                waited_s,
                message[:300].encode("utf-8", "replace").decode("utf-8"),
            ),
        )


def limit_rows(home: Path, since_day: str) -> list[tuple]:
    """(ts, agent, task, reset_at, waited_s, message), newest first."""
    db = _db(home)
    try:
        return db.execute(
            "select ts, agent, task, reset_at, waited_s, message from limits where day >= ? "
            "order by ts desc",
            (since_day,),
        ).fetchall()
    finally:
        db.close()


def last_row(home: Path) -> int:
    """Marks the start of a run: spend_since(home, mark) is what the run used."""
    db = _db(home)
    try:
        return db.execute("select coalesce(max(rowid), 0) from usage").fetchone()[0]
    finally:
        db.close()


def spend_since(home: Path, rowid: int) -> list[tuple]:
    """(task, agent, billed, calls, usd, tokens_in, tokens_out) of rows after the mark."""
    db = _db(home)
    try:
        return db.execute(
            """select task, agent, billed, count(*), sum(usd), sum(tokens_in), sum(tokens_out)
            from usage where rowid > ? group by task, agent, billed order by task, agent""",
            (rowid,),
        ).fetchall()
    finally:
        db.close()
