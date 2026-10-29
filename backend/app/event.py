"""Event organizations with guest access codes (DEMO1, ADR 0034).

    uv run python -m app.event create --name launchpad --expires 2026-11-06T23:59-05:00 \
        --max-guests 300 --runs <run id>,<run id>

`create` is the only way to make an event organization (`organization.is_event`, immutable after
INSERT). It checks that every run exists and is done, then writes the organization, the code's
SHA-256 and the shared runs in one transaction, and prints the code once. Any error exits 2 and
writes nothing. `purge` arrives in DEMO1 part 2.
"""

import argparse
import hashlib
import secrets
import sys
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import engine_for
from .models import EventCode, EventCodeRun, Organization, Run, now
from .settings import get_settings

ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I/L: read aloud and typed on a phone
CODE_LENGTH = 8  # 31^8 ≈ 8.5e11; guessing is also rate-limited per IP and per code (part 2)
DEFAULT_ORG = "LaunchPad demo"


def new_code() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(CODE_LENGTH))


def code_sha256(code: str) -> str:
    """The stored form of a code; input is normalised the same way at sign-in."""
    return hashlib.sha256(code.strip().upper().encode()).hexdigest()


def expires(value: str) -> datetime:
    t = datetime.fromisoformat(value)
    if t.tzinfo is None:
        raise argparse.ArgumentTypeError("needs a UTC offset, e.g. 2026-11-06T23:59-05:00")
    return t.astimezone(UTC)


def run_ids(value: str) -> list[str]:
    ids = [i.strip() for i in value.split(",") if i.strip()]
    if not ids:
        raise argparse.ArgumentTypeError("at least one run id")
    return list(dict.fromkeys(ids))


def positive(value: str) -> int:
    n = int(value)
    if n < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return n


def create(
    db: Session, name: str, org_name: str, expires_at: datetime, max_guests: int, runs: list[str]
) -> str:
    """Write the event and return its code; ValueError (nothing written) on bad input."""
    if expires_at <= now():
        raise ValueError(f"--expires {expires_at.isoformat()} is in the past")
    found = {r.id: r.status for r in db.scalars(select(Run).where(Run.id.in_(runs)))}
    if missing := [r for r in runs if r not in found]:
        raise ValueError(f"unknown run id: {', '.join(missing)}")
    if not_done := [r for r in runs if found[r] != "done"]:
        raise ValueError(f"run not processed (status is not done): {', '.join(not_done)}")
    if db.scalar(select(EventCode).where(EventCode.name == name)) is not None:
        raise ValueError(f"event '{name}' exists")
    if db.scalar(select(Organization).where(Organization.name == org_name)) is not None:
        raise ValueError(f"organization '{org_name}' exists")
    code = new_code()
    org = Organization(name=org_name, is_event=True)  # the only place is_event is set
    db.add(org)
    db.flush()
    event = EventCode(
        organization_id=org.id,
        name=name,
        code_sha256=code_sha256(code),
        expires_at=expires_at,
        max_guests=max_guests,
    )
    db.add(event)
    db.flush()
    db.add_all(EventCodeRun(event_code_id=event.id, run_id=r) for r in runs)
    db.commit()
    return code


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m app.event", description="Event guest access.")
    sub = p.add_subparsers(dest="command", required=True)
    c = sub.add_parser("create", help="create an event organization and print its guest code")
    c.add_argument("--name", required=True, help="event name, e.g. launchpad")
    c.add_argument("--org", default=DEFAULT_ORG, help=f"organization name (default: {DEFAULT_ORG})")
    c.add_argument("--expires", required=True, type=expires, help="ISO 8601 with UTC offset")
    c.add_argument("--max-guests", required=True, type=positive)
    c.add_argument("--runs", required=True, type=run_ids, help="comma-separated run ids")
    pg = sub.add_parser("purge", help="delete all guest data of an event (DEMO1 part 2)")
    pg.add_argument("--name", required=True)
    return p


def main(argv: Sequence[str] | None = None) -> None:
    p = parser()
    args = p.parse_args(argv)
    if args.command == "purge":
        p.error("purge is not built yet (DEMO1 part 2)")
    with Session(engine_for(get_settings().database_url)) as db:
        try:
            code = create(db, args.name, args.org, args.expires, args.max_guests, args.runs)
        except ValueError as e:
            p.error(str(e))  # exit 2; the session rolls back, nothing was committed
    print(f"event '{args.name}': organization '{args.org}', {len(args.runs)} shared run(s)")
    print(f"expires {args.expires.isoformat()}, at most {args.max_guests} guests")
    print(f"code: {code}")
    print("The code is shown only now; only its SHA-256 is stored.", file=sys.stderr)


if __name__ == "__main__":
    main()
