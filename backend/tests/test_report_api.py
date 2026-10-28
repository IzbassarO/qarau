"""PDF inspection report route (RPT1 part 3): GET /api/inspections/{id}/report.pdf on the
fixture bundle of test_findings_api.py with 2 manual findings (1 confirmed, 2 rejected), a
colour layer and frame images, and a fixed report clock. Expected values are literals."""

import hashlib
import io
import json
import re
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from conftest import bearer
from fastapi.testclient import TestClient
from PIL import Image
from pypdf import PdfReader
from qarau_pipeline.cli import main
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_findings_api import BODY, ENG, POINTS, VIEW, marked  # noqa: F401  (fixture)

from app.db import engine_for
from app.main import app
from app.models import Report
from app.reports import report_clock

URL = "/api/inspections/1/report.pdf"
REPORT_TIME = datetime(2026, 10, 26, 14, 32, tzinfo=timezone(timedelta(hours=5)))
REPORT_TIME_ISO = "2026-10-26T14:32:00+05:00"
CRACK = {"class": "crack", "severity": "critical", "point_indices": [0, 1, 4, 5]}
REJECT = {"verdict": "reject", "comment": "shadow, not a crack"}
# Section headings and the coverage line in print order (cards by severity: critical 2 first),
# as each language prints them.
SECTIONS = {
    "en": ["Inspection report", "Executive summary", "Findings by severity", "Finding details",
           "Finding 2", "Decision log", "Finding 1", "Decision log", "Not inspected areas",
           "Coverage not computed.", "Method and limitations", "Sign-off", "Document control"],
    "ru": ["Отчёт об инспекции", "Краткое резюме", "Дефекты по критичности", "Описание дефектов",
           "Дефект 2", "Журнал решений", "Дефект 1", "Журнал решений", "Неосмотренные участки",
           "Покрытие не рассчитано.", "Метод и ограничения", "Подписи", "Управление документом"],
    "kk": ["Тексеру есебі", "Қысқаша түйін", "Маңыздылығы бойынша ақаулар", "Ақаулар сипаттамасы",
           "Ақау 2", "Шешімдер журналы", "Ақау 1", "Шешімдер журналы", "Тексерілмеген аймақтар",
           "Қамту есептелмеген.", "Әдіс және шектеулер", "Қолтаңбалар", "Құжатты басқару"],
}  # fmt: skip


@pytest.fixture
def reported(marked: TestClient, runs_root: Path) -> TestClient:  # noqa: F811
    """Inspection 1: finding 1 (corrosion, watch) confirmed, finding 2 (crack, critical) rejected,
    both by the engineer; the bundle gets a colour layer and the 4 frame images; the report clock
    is fixed at REPORT_TIME."""
    run = runs_root / "fixture-run"
    (run / "web/points.color.u8").write_bytes(bytes([90, 90, 90] * len(POINTS)))
    man = json.loads((run / "web/manifest.json").read_text())
    man["points"]["color"] = "web/points.color.u8"
    (run / "web/manifest.json").write_text(json.dumps(man))
    (run / "frames").mkdir()
    for i in range(4):
        Image.new("RGB", (100, 100), (40 * i, 80, 120)).save(run / f"frames/{i:06d}.jpg")
    for body in (BODY, CRACK):
        assert marked.post("/api/inspections/1/findings", json=body, headers=ENG).status_code == 201
    for f, verdict in ((1, {"verdict": "confirm"}), (2, REJECT)):
        r = marked.post(f"/api/findings/{f}/decisions", json=verdict, headers=ENG)
        assert r.status_code == 201, r.text
    app.dependency_overrides[report_clock] = lambda: REPORT_TIME
    return marked


def text_of(pdf: bytes) -> str:
    return re.sub(r"\s+", " ", " ".join(p.extract_text() for p in PdfReader(io.BytesIO(pdf)).pages))


def stored(db_url: str) -> list[Report]:
    with Session(engine_for(db_url)) as db:
        return list(db.scalars(select(Report).order_by(Report.id)))


def utc(t: datetime) -> datetime:
    return t if t.tzinfo is not None else t.replace(tzinfo=UTC)  # SQLite gives naive UTC


def test_viewer_gets_the_pdf_and_no_token_is_401(reported: TestClient, db_url: str) -> None:
    r = reported.get(URL, params={"lang": "en"}, headers=VIEW)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/pdf"
    assert r.content[:5] == b"%PDF-"
    assert r.headers["content-disposition"] == 'inline; filename="QV-2026-0001-v1-en.pdf"'
    assert r.headers["x-report-generated-at"] == REPORT_TIME_ISO
    assert reported.get(URL, params={"lang": "en"}).status_code == 401
    bad = {"Authorization": "Bearer nope"}
    assert reported.get(URL, params={"lang": "en"}, headers=bad).status_code == 401
    assert len(stored(db_url)) == 1  # refused calls log nothing


def test_unknown_inspection_404_bad_lang_422(reported: TestClient, db_url: str) -> None:
    r = reported.get("/api/inspections/99/report.pdf", params={"lang": "en"}, headers=VIEW)
    assert r.status_code == 404
    assert reported.get(URL, params={"lang": "de"}, headers=VIEW).status_code == 422
    assert reported.get(URL, headers=VIEW).status_code == 422
    assert stored(db_url) == []


def test_stored_hash_equals_header_and_body_and_repeats(reported: TestClient, db_url: str) -> None:
    first = reported.get(URL, params={"lang": "ru"}, headers=VIEW)
    second = reported.get(URL, params={"lang": "ru"}, headers=bearer("engineer"))
    assert first.status_code == second.status_code == 200
    sha = first.headers["x-report-sha256"]
    assert re.fullmatch(r"[0-9a-f]{64}", sha)
    assert sha == hashlib.sha256(first.content).hexdigest()
    assert second.headers["x-report-sha256"] == sha and second.content == first.content
    rows = stored(db_url)
    assert len(rows) == 2
    for row, user in zip(rows, (3, 2), strict=True):  # seeded viewer 3, engineer 2
        assert (row.inspection_id, row.lang, row.sha256) == (1, "ru", sha)
        assert (row.report_number, row.version, row.created_by) == ("QV-2026-0001", 1, user)
        assert utc(row.generated_at) == datetime(2026, 10, 26, 9, 32, tzinfo=UTC)
    # the shared read-only token has no user: the row says so
    shared = reported.get(URL, params={"lang": "ru"}, headers=bearer("shared-dev-token"))
    assert shared.status_code == 200 and shared.headers["x-report-sha256"] == sha
    assert stored(db_url)[-1].created_by is None


def test_new_decision_or_report_time_changes_the_hash(reported: TestClient) -> None:
    before = reported.get(URL, params={"lang": "en"}, headers=VIEW).headers["x-report-sha256"]
    app.dependency_overrides[report_clock] = lambda: REPORT_TIME + timedelta(minutes=1)
    later = reported.get(URL, params={"lang": "en"}, headers=VIEW).headers["x-report-sha256"]
    app.dependency_overrides[report_clock] = lambda: REPORT_TIME
    assert later != before
    r = reported.post("/api/findings/2/decisions", json={"verdict": "confirm"}, headers=ENG)
    assert r.status_code == 201
    after = reported.get(URL, params={"lang": "en"}, headers=VIEW).headers["x-report-sha256"]
    assert after != before


@pytest.mark.parametrize("lang", ["ru", "kk", "en"])
def test_route_pdf_has_every_section(reported: TestClient, lang: str) -> None:
    r = reported.get(URL, params={"lang": lang}, headers=VIEW)
    assert r.status_code == 200
    text = text_of(r.content)
    pos = 0
    for heading in SECTIONS[lang]:
        found = text.find(heading, pos)
        assert found >= 0, f"{lang}: {heading!r} missing or out of order"
        pos = found + len(heading)
    assert "QV-2026-0001" in text and "engineer" in text and "shadow, not a crack" in text


def test_cli_file_equals_route_body(reported: TestClient, db_url: str, runs_root: Path) -> None:
    """The CLI and the route share loader and renderer: same inspection, lang and report time,
    same bytes."""
    body = reported.get(URL, params={"lang": "kk"}, headers=VIEW)
    assert body.status_code == 200
    out = runs_root.parent / "cli.pdf"
    main(["report", "--inspection", "1", "--lang", "kk", "--out", str(out), "--db", db_url,
          "--report-time", REPORT_TIME_ISO, "--runs-dir", str(runs_root)])  # fmt: skip
    assert out.read_bytes() == body.content
    assert hashlib.sha256(out.read_bytes()).hexdigest() == body.headers["x-report-sha256"]
