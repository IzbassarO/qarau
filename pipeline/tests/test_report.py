"""PDF inspection report (RPT1 part 1): sections, languages, determinism, fonts, translations,
the database reader and the CLI. Expected strings are literals here, never read from the code."""

import io
import re
import sqlite3
import tomllib
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from pypdf import PdfReader

from qarau_pipeline.cli import main
from qarau_pipeline.report import ReportInput, load_report_input, load_strings, render_pdf
from qarau_pipeline.report.inputs import DecisionEntry, Evidence, FindingInput, InspectionInput

REPORT_DIR = Path(__file__).resolve().parents[1] / "qarau_pipeline" / "report"
ALMATY = timezone(timedelta(hours=5))
REPORT_TIME = datetime(2026, 10, 26, 14, 32, tzinfo=ALMATY)
OUTLINE = ((10.0, 20.0), (60.0, 20.0), (35.0, 70.0))

INPUT = ReportInput(
    organization="Caspian Inspection Services",
    site="Tank T-104",
    inspection=InspectionInput(7, "quick_scan", datetime(2026, 10, 21, 4, 41, tzinfo=UTC)),
    engineer="Dana Serik",
    findings=(
        FindingInput(
            id=3, class_="corrosion", severity="critical", status="confirmed", area_m2=0.42,
            scale_source="manual: Letter sheet", run_dir="notre-dame-p3e",
            evidence=(Evidence(412, OUTLINE), Evidence(415, OUTLINE), Evidence(418, OUTLINE)),
            decisions=(
                DecisionEntry("Aslan Bekov", "confirm", None, None, None,
                              datetime(2026, 10, 21, 5, 2, tzinfo=UTC)),
            ),
        ),
        FindingInput(
            id=4, class_="coating", severity="watch", status="rejected", area_m2=None,
            scale_source=None, run_dir="notre-dame-p3e",
            evidence=(Evidence(12, OUTLINE),),
            decisions=(
                DecisionEntry("Dana Serik", "reject", "Shadow, not corrosion", None, None,
                              datetime(2026, 10, 21, 5, 20, tzinfo=UTC)),
            ),
        ),
    ),
)  # fmt: skip

# Literal expectations per language: the headings in the order they must appear, then strings
# that must appear somewhere.
HEADINGS = {
    "en": ["Inspection report", "Object", "Map", "Findings", "Finding 3", "Decision log",
           "Finding 4", "Decision log", "Not-seen areas", "Engineer: Dana Serik."],
    "ru": ["Отчёт об инспекции", "Объект контроля", "Карта", "Дефекты", "Дефект 3",
           "Журнал решений", "Дефект 4", "Журнал решений", "Неосмотренные участки",
           "Инженер: Dana Serik."],
    "kk": ["Тексеру есебі", "Бақылау нысаны", "Карта", "Ақаулар", "Ақау 3", "Шешімдер журналы",
           "Ақау 4", "Шешімдер журналы", "Көрінбеген аймақтар", "Инженер: Dana Serik."],
}  # fmt: skip
SCALE_NOT_SET = {"en": "scale not set", "ru": "масштаб не задан", "kk": "масштаб орнатылмаған"}
COVERAGE = {
    "en": "Coverage not computed.",
    "ru": "Покрытие не рассчитано.",
    "kk": "Қамту есептелмеген.",
}
AREA = {"en": "0.42 m²", "ru": "0,42 м²", "kk": "0,42 м²"}
REPORT_TIME_TEXT = {
    "en": "26 October 2026 at 14:32 (UTC+05:00)",
    "ru": "26 октября 2026 г., 14:32 (UTC+05:00)",
    "kk": "2026 жылғы 26 қазан, 14:32 (UTC+05:00)",
}


def text_of(pdf: bytes) -> str:
    pages = PdfReader(io.BytesIO(pdf)).pages
    return re.sub(r"\s+", " ", " ".join(p.extract_text() for p in pages))


@pytest.fixture(scope="module")
def pdfs() -> dict[str, bytes]:
    return {lang: render_pdf(INPUT, load_strings(lang), REPORT_TIME) for lang in HEADINGS}


@pytest.mark.parametrize("lang", ["ru", "kk", "en"])
def test_sections_in_order(pdfs: dict[str, bytes], lang: str) -> None:
    text = text_of(pdfs[lang])
    pos = 0
    for heading in HEADINGS[lang]:
        found = text.find(heading, pos)
        assert found >= 0, f"{lang}: {heading!r} missing or out of order"
        pos = found + len(heading)
    assert SCALE_NOT_SET[lang] in text
    assert COVERAGE[lang] in text
    assert AREA[lang] in text
    assert REPORT_TIME_TEXT[lang] in text
    assert "manual: Letter sheet" in text
    assert "Shadow, not corrosion" in text


def test_kazakh_letters_survive(pdfs: dict[str, bytes]) -> None:
    text = text_of(pdfs["kk"])
    for letter in "қәғңөүіҰ":
        assert letter in text, letter


def test_same_inputs_same_bytes(pdfs: dict[str, bytes]) -> None:
    again = render_pdf(INPUT, load_strings("en"), REPORT_TIME)
    assert again == pdfs["en"]
    later = render_pdf(INPUT, load_strings("en"), REPORT_TIME + timedelta(minutes=1))
    assert later != pdfs["en"]
    meta = PdfReader(io.BytesIO(pdfs["en"])).metadata
    assert meta is not None
    assert meta["/CreationDate"] == "D:20261026143200+05'00'"
    assert meta["/ModDate"] == "D:20261026143200+05'00'"


def test_every_font_is_embedded(pdfs: dict[str, bytes]) -> None:
    names = set()
    for page in PdfReader(io.BytesIO(pdfs["kk"])).pages:
        resources: Any = page["/Resources"]
        for ref in resources["/Font"].values():
            font: Any = ref.get_object()
            if font["/Subtype"] == "/Type0":
                font = font["/DescendantFonts"][0].get_object()
            desc = font["/FontDescriptor"].get_object()
            assert any(k in desc for k in ("/FontFile", "/FontFile2", "/FontFile3")), font
            names.add(str(font["/BaseFont"]))
    joined = " ".join(names)
    for family in ("Geologica", "IBMPlexSans"):
        assert family in joined, names


def test_translation_tables_have_the_same_keys() -> None:
    table = tomllib.loads((REPORT_DIR / "translations.toml").read_text())
    assert set(table) == {"ru", "kk", "en"}
    assert set(table["ru"]) == set(table["en"]) == set(table["kk"])
    for lang in table:
        assert len(table[lang]["months"]) == 12


def test_no_visible_string_is_hard_coded() -> None:
    """Every translation replaced by a marker: what is left on the page is markers and the
    fixture's own data (names, comments, numbers), nothing written in render.py."""
    keys = list(load_strings("en"))
    # short markers, so none is wider than a table column and gets split across lines
    marked: dict[str, Any] = {k: f"@k{i}@" for i, k in enumerate(keys)}
    marked["months"] = [f"@m{i}@" for i in range(12)]
    marked["decimal"] = "."
    text = text_of(render_pdf(INPUT, marked, REPORT_TIME))
    assert marked["section_findings"] in text and marked["col_engineer"] in text
    rest = re.sub(r"@[km]\d+@", " ", text)
    for data in ("Caspian Inspection Services", "Tank T-104", "Dana Serik", "Aslan Bekov",
                 "manual: Letter sheet", "Shadow, not corrosion"):  # fmt: skip
        rest = rest.replace(data, " ")
    assert not re.search(r"[^\W\d_]", rest), rest


def make_db(path: Path) -> str:
    """The columns the report reads, as the API's migrations create them, with 2 manual
    findings: #1 confirmed then reclassified to watch (scale set), #2 rejected (no scale)."""
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE organization (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE "user" (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE site (id INTEGER PRIMARY KEY, org_id INTEGER, name TEXT);
        CREATE TABLE inspection (id INTEGER PRIMARY KEY, site_id INTEGER, mode TEXT,
                                 created_at DATETIME);
        CREATE TABLE run (id TEXT PRIMARY KEY, inspection_id INTEGER, run_dir TEXT);
        CREATE TABLE finding (id INTEGER PRIMARY KEY, run_id TEXT, class TEXT, severity TEXT,
                              status TEXT, area_m2 FLOAT, scale_source TEXT);
        CREATE TABLE observation (id INTEGER PRIMARY KEY, finding_id INTEGER, frame_id INTEGER,
                                  outline JSON);
        CREATE TABLE decision (id INTEGER PRIMARY KEY, finding_id INTEGER, user_id INTEGER,
                               verdict TEXT, comment TEXT, new_class TEXT, new_severity TEXT,
                               created_at DATETIME);
        INSERT INTO organization VALUES (1, 'Caspian Inspection Services');
        INSERT INTO "user" VALUES (1, 'Aslan Bekov'), (2, 'Dana Serik');
        INSERT INTO site VALUES (1, 1, 'Tank T-104');
        INSERT INTO inspection VALUES (7, 1, 'quick_scan', '2026-10-21 04:41:00.000000');
        INSERT INTO run VALUES ('r1', 7, 'notre-dame-p3e');
        INSERT INTO finding VALUES (1, 'r1', 'corrosion', 'critical', 'proposed', 0.42,
                                    'manual: Letter sheet');
        INSERT INTO finding VALUES (2, 'r1', 'coating', 'critical', 'proposed', NULL, NULL);
        INSERT INTO observation VALUES (1, 1, 412, '[[10, 20], [60, 20], [35, 70]]'),
                                       (2, 1, 415, '[[1, 2], [3, 4], [5, 6]]'),
                                       (3, 2, 12, NULL);
        INSERT INTO decision VALUES
            (1, 1, 1, 'confirm', NULL, NULL, NULL, '2026-10-21 05:02:00.000000'),
            (2, 2, 2, 'reject', 'Shadow, not corrosion', NULL, NULL, '2026-10-21 05:20:00.000000'),
            (3, 1, 1, 'reclass', 'Grew since May', NULL, 'watch', '2026-10-21 05:10:00.000000');
    """)  # fmt: skip
    con.commit()
    con.close()
    return f"sqlite:///{path}"


def test_load_report_input(tmp_path: Path) -> None:
    engine = sa.create_engine(make_db(tmp_path / "api.db"))
    inp = load_report_input(engine, 7)
    assert (inp.organization, inp.site, inp.engineer) == (
        "Caspian Inspection Services", "Tank T-104", "Dana Serik",
    )  # fmt: skip
    created = datetime(2026, 10, 21, 4, 41, tzinfo=UTC)
    assert inp.inspection == InspectionInput(7, "quick_scan", created)
    assert inp.coverage is None
    one, two = inp.findings
    assert (one.id, one.class_, one.severity, one.status) == (1, "corrosion", "watch", "confirmed")
    assert (one.area_m2, one.scale_source) == (0.42, "manual: Letter sheet")
    assert [e.frame_id for e in one.evidence] == [412, 415]
    assert one.evidence[0].outline == ((10.0, 20.0), (60.0, 20.0), (35.0, 70.0))
    assert [d.verdict for d in one.decisions] == ["confirm", "reclass"]
    assert one.decisions[1].time == datetime(2026, 10, 21, 5, 10, tzinfo=UTC)
    assert (two.status, two.severity, two.area_m2, two.scale_source) == (
        "rejected", "critical", None, None,
    )  # fmt: skip
    assert two.evidence[0].outline == ()
    assert [(d.user, d.comment) for d in two.decisions] == [("Dana Serik", "Shadow, not corrosion")]


def test_cli_writes_the_same_file_twice(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    url = make_db(tmp_path / "api.db")
    when = "2026-10-26T14:32:00+05:00"
    args = ["report", "--inspection", "7", "--db", url, "--report-time", when]
    main([*args, "--lang", "kk", "--out", str(tmp_path / "a.pdf")])
    main([*args, "--lang", "kk", "--out", str(tmp_path / "b.pdf")])
    a, b = (tmp_path / "a.pdf").read_bytes(), (tmp_path / "b.pdf").read_bytes()
    assert a[:5] == b"%PDF-" and a == b
    text = text_of(a)
    assert "Ақау 1" in text and "Ақау 2" in text and "Қамту есептелмеген." in text
    assert "масштаб орнатылмаған" in text
    assert "sha256" in capsys.readouterr().out


def test_cli_unknown_inspection_fails(tmp_path: Path) -> None:
    url = make_db(tmp_path / "api.db")
    with pytest.raises(SystemExit) as err:
        main(["report", "--inspection", "99", "--db", url, "--lang", "en",
              "--out", str(tmp_path / "x.pdf")])  # fmt: skip
    assert err.value.code == 1
    assert not (tmp_path / "x.pdf").exists()
