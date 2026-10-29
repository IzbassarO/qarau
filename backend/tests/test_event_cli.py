"""`python -m app.event create` (DEMO1 part 1): prints a code, makes the event organization and
stores only the code's SHA-256; bad input exits 2 and writes nothing."""

import hashlib
import re
from datetime import UTC, datetime

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from app import event
from app.db import engine_for
from app.models import EventCode, EventCodeRun, Inspection, Mode, Organization, Run, Site

CREATE = ["create", "--name", "launchpad", "--expires", "2026-11-06T23:59-05:00",
          "--max-guests", "300", "--runs"]  # fmt: skip
CODE_LINE = re.compile(r"^code: ([A-HJKMNP-Z2-9]{8})$", re.MULTILINE)


@pytest.fixture
def runs(db_url: str, monkeypatch: pytest.MonkeyPatch) -> str:
    """Two runs of a normal organization: "run-done" (processed) and "run-up" (uploaded). The
    clock is fixed at 2026-10-28 so the literal expiry stays in the future."""
    monkeypatch.setattr(event, "now", lambda: datetime(2026, 10, 28, 12, 0, tzinfo=UTC))
    with Session(engine_for(db_url)) as db:
        org = Organization(name="Customer")
        db.add(org)
        db.flush()
        site = Site(org_id=org.id, name="s")
        db.add(site)
        db.flush()
        insp = Inspection(site_id=site.id, mode=Mode.quick_scan)
        db.add(insp)
        db.flush()
        db.add(Run(id="run-done", inspection_id=insp.id, run_dir="run-done", status="done"))
        db.add(Run(id="run-up", inspection_id=insp.id, run_dir="run-up", status="uploaded"))
        db.commit()
    return db_url


def all_rows(db_url: str) -> str:
    """Every row of every table, as text."""
    engine = engine_for(db_url)
    out = []
    with engine.connect() as conn:
        for table in inspect(engine).get_table_names():
            out += [repr(r) for r in conn.execute(text(f'SELECT * FROM "{table}"'))]  # noqa: S608
    return "\n".join(out)


def counts(db_url: str) -> tuple[int, ...]:
    """Rows in organization, event_code, event_code_run."""
    with engine_for(db_url).connect() as conn:
        return tuple(
            conn.execute(text(f"SELECT count(*) FROM {t}")).scalar_one()  # noqa: S608
            for t in ("organization", "event_code", "event_code_run")
        )


def test_create_prints_a_code_and_makes_the_event_organization(
    runs: str, capsys: pytest.CaptureFixture[str]
) -> None:
    event.main([*CREATE, "run-done"])
    out = capsys.readouterr().out
    match = CODE_LINE.search(out)
    assert match is not None, out
    code = match.group(1)
    with Session(engine_for(runs)) as db:
        org = db.query(Organization).filter_by(name="LaunchPad demo").one()
        assert org.is_event is True
        ev = db.query(EventCode).one()
        expires = ev.expires_at if ev.expires_at.tzinfo else ev.expires_at.replace(tzinfo=UTC)
        assert (ev.organization_id, ev.name, ev.max_guests) == (org.id, "launchpad", 300)
        assert expires == datetime(2026, 11, 7, 4, 59, tzinfo=UTC)
        assert ev.code_sha256 == hashlib.sha256(code.encode()).hexdigest()
        shared = [(r.event_code_id, r.run_id) for r in db.query(EventCodeRun)]
        assert shared == [(ev.id, "run-done")]
        customer = db.query(Organization).filter_by(name="Customer").one()
        assert customer.is_event is False
    assert code not in all_rows(runs)  # only the hash is stored


def test_two_creates_give_different_codes(runs: str, capsys: pytest.CaptureFixture[str]) -> None:
    event.main([*CREATE, "run-done"])
    event.main(["create", "--name", "second", "--org", "Second demo", "--expires",
                "2026-11-06T23:59-05:00", "--max-guests", "5", "--runs", "run-done"])  # fmt: skip
    codes = CODE_LINE.findall(capsys.readouterr().out)
    assert len(codes) == 2 and codes[0] != codes[1]
    assert counts(runs) == (3, 2, 2)


@pytest.mark.parametrize(
    "argv",
    [
        [*CREATE, "run-done,no-such-run"],  # unknown run id
        [*CREATE, "run-up"],  # not processed
        [*CREATE, ""],  # no run
        ["create", "--name", "launchpad", "--expires", "2026-11-06T23:59", "--max-guests",
         "300", "--runs", "run-done"],  # no UTC offset
        ["create", "--name", "launchpad", "--expires", "2026-10-01T00:00+05:00", "--max-guests",
         "300", "--runs", "run-done"],  # in the past
        ["create", "--name", "launchpad", "--expires", "2026-11-06T23:59-05:00", "--max-guests",
         "0", "--runs", "run-done"],
        ["purge", "--name", "launchpad"],  # part 2
    ],
)  # fmt: skip
def test_bad_input_exits_2_and_writes_nothing(
    runs: str, argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exc:
        event.main(argv)
    assert exc.value.code == 2
    out = capsys.readouterr()
    assert "code:" not in out.out
    assert counts(runs) == (1, 0, 0)


def test_unknown_run_is_named(runs: str, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        event.main([*CREATE, "run-done,no-such-run"])
    assert "unknown run id: no-such-run" in capsys.readouterr().err


def test_an_existing_event_or_organization_name_is_refused(
    runs: str, capsys: pytest.CaptureFixture[str]
) -> None:
    event.main([*CREATE, "run-done"])
    with pytest.raises(SystemExit) as exc:  # same event name
        event.main([*CREATE, "run-done"])
    assert exc.value.code == 2
    with pytest.raises(SystemExit) as exc:  # new event, same organization name
        event.main(["create", "--name", "other", "--expires", "2026-11-06T23:59-05:00",
                    "--max-guests", "1", "--runs", "run-done"])  # fmt: skip
    assert exc.value.code == 2
    assert counts(runs) == (2, 1, 1)
