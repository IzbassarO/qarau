"""PDF inspection report (RPT1 part 1): sections, languages, page header/footer, PDF/A, fonts,
determinism, translations, the database reader and the CLI. Expected strings are literals here,
never read from the code under test."""

import io
import re
import sqlite3
import string
import tomllib
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from pypdf import PdfReader

from qarau_pipeline.cli import main
from qarau_pipeline.report import ReportInput, load_report_input, render_pdf, render_report
from qarau_pipeline.report.inputs import (
    DecisionEntry,
    Evidence,
    FindingInput,
    InspectionInput,
    VersionEntry,
)

REPORT_DIR = Path(__file__).resolve().parents[1] / "qarau_pipeline" / "report"
ALMATY = timezone(timedelta(hours=5))
REPORT_TIME = datetime(2026, 10, 26, 14, 32, tzinfo=ALMATY)
OUTLINE = ((10.0, 20.0), (60.0, 20.0), (35.0, 70.0))
KK_COMMENT = "Ішкі қабырға. Үлкен дақ. Ұзындығы шамалы."  # data in Kazakh, with capitals


def finding(id: int, class_: str, severity: str, status: str, area: float | None,
            scale: str | None, pct: float | None, decisions: tuple[DecisionEntry, ...],
            frames: tuple[int, ...]) -> FindingInput:  # fmt: skip
    return FindingInput(
        id=id, class_=class_, severity=severity, status=status, area_m2=area, scale_source=scale,
        area_uncertainty_pct=pct, run_dir="notre-dame-p3e",
        evidence=tuple(Evidence(f, OUTLINE) for f in frames), decisions=decisions,
    )  # fmt: skip


INPUT = ReportInput(
    organization="Caspian Inspection Services",
    site="Tank T-104",
    asset="Vertical storage tank T-104",
    inspection=InspectionInput(7, "quick_scan", datetime(2026, 10, 21, 4, 41, tzinfo=UTC)),
    capture_date=datetime(2026, 10, 21, 4, 41, tzinfo=UTC),
    report_number="QV-2026-0007",
    version=1,
    engineer="Dana Serik",
    report_time=REPORT_TIME,
    findings=(
        # given out of severity order: the report sorts critical first
        finding(5, "corrosion", "watch", "confirmed", 0.19, "manual: Letter sheet", None, (
            DecisionEntry("Aslan Bekov", "confirm", KK_COMMENT, None, None,
                          datetime(2026, 10, 21, 5, 4, tzinfo=UTC)),
        ), (501, 502, 503, 504)),
        finding(3, "corrosion", "critical", "confirmed", 0.42, "manual: Letter sheet", 2.0, (
            DecisionEntry("Aslan Bekov", "confirm", None, None, None,
                          datetime(2026, 10, 21, 5, 2, tzinfo=UTC)),
        ), (412, 415, 418)),
        finding(4, "coating", "watch", "rejected", None, None, None, (
            DecisionEntry("Dana Serik", "reject", "Shadow, not corrosion", None, None,
                          datetime(2026, 10, 21, 5, 20, tzinfo=UTC)),
        ), (12,)),
    ),
    versions=(VersionEntry(1, REPORT_TIME, "Dana Serik"),),
    verification_id="7F3A-09C2-B4E1-5D68",
)  # fmt: skip

# Literal expectations per language: the headings in the order they must appear in the PDF.
HEADINGS = {
    "en": ["Inspection report", "Executive summary", "Map", "Findings by severity",
           "Finding details", "Finding 3", "Decision log", "Finding 4", "Decision log",
           "Finding 5", "Decision log", "Not inspected areas", "Method and limitations",
           "Sign-off", "Prepared", "Checked", "Approved", "Document control"],
    "ru": ["Отчёт об инспекции", "Краткое резюме", "Карта", "Дефекты по критичности",
           "Описание дефектов", "Дефект 3", "Журнал решений", "Дефект 4", "Журнал решений",
           "Дефект 5", "Журнал решений", "Неосмотренные участки", "Метод и ограничения",
           "Подписи", "Составил", "Проверил", "Утвердил", "Управление документом"],
    "kk": ["Тексеру есебі", "Қысқаша түйін", "Карта", "Маңыздылығы бойынша ақаулар",
           "Ақаулар сипаттамасы", "Ақау 3", "Шешімдер журналы", "Ақау 4", "Шешімдер журналы",
           "Ақау 5", "Шешімдер журналы", "Тексерілмеген аймақтар", "Әдіс және шектеулер",
           "Қолтаңбалар", "Дайындаған", "Тексерген", "Бекіткен", "Құжатты басқару"],
}  # fmt: skip
# Strings that must appear somewhere.
EXPECTED = {
    "en": ["Coverage not computed.", "scale not set", "0.42 m² ± 2 %",
           "0.19 m², uncertainty not estimated", "Vertical storage tank T-104",
           "26 October 2026 at 14:32 (UTC+05:00)", "21 October 2026 at 09:41 (UTC+05:00)",
           "Engineer: Dana Serik. Report time: 26 October 2026 at 14:32 (UTC+05:00).",
           "Report QV-2026-0007, version 1", "Frame 412, Frame 415, Frame 418",
           "Aslan Bekov, 21 October 2026 at 10:02 (UTC+05:00)", "First issue",
           "7F3A-09C2-B4E1-5D68", "Shadow, not corrosion"],
    "ru": ["Покрытие не рассчитано.", "масштаб не задан", "0,42 м² ± 2 %",
           "0,19 м², погрешность не оценена", "26 октября 2026 г., 14:32 (UTC+05:00)",
           "Инженер: Dana Serik. Время отчёта: 26 октября 2026 г., 14:32 (UTC+05:00).",
           "Отчёт QV-2026-0007, версия 1", "Кадр 412, Кадр 415, Кадр 418", "Первый выпуск"],
    "kk": ["Қамту есептелмеген.", "масштаб орнатылмаған", "0,42 м² ± 2 %",
           "0,19 м², қателігі бағаланбаған", "2026 жылғы 26 қазан, 14:32 (UTC+05:00)",
           "Инженер: Dana Serik. Есеп уақыты: 2026 жылғы 26 қазан, 14:32 (UTC+05:00).",
           "Есеп QV-2026-0007, 1-нұсқа", "Кадр 412, Кадр 415, Кадр 418", "Алғашқы шығарылым"],
}  # fmt: skip
# "page 1 of N" on page 1, as each language says it
PAGE_ONE = {
    "en": r"Page 1 of (\d+)",
    "ru": r"Страница 1 из (\d+)",
    "kk": r"(\d+) беттің 1-беті",
}
LAST_PAGE = {"en": "Page {n} of {n}", "ru": "Страница {n} из {n}", "kk": "{n} беттің {n}-беті"}


def flat(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def text_of(pdf: bytes) -> str:
    return flat(" ".join(p.extract_text() for p in PdfReader(io.BytesIO(pdf)).pages))


@pytest.fixture(scope="module")
def pdfs() -> dict[str, bytes]:
    return {lang: render_report(INPUT, lang) for lang in HEADINGS}


@pytest.mark.parametrize("lang", ["ru", "kk", "en"])
def test_sections_in_order(pdfs: dict[str, bytes], lang: str) -> None:
    text = text_of(pdfs[lang])
    pos = 0
    for heading in HEADINGS[lang]:
        found = text.find(heading, pos)
        assert found >= 0, f"{lang}: {heading!r} missing or out of order"
        pos = found + len(heading)
    for s in EXPECTED[lang]:
        assert s in text, f"{lang}: {s!r} missing"
    assert "manual: Letter sheet" in text
    assert "Frame 504" not in text and "Кадр 504" not in text  # at most 3 evidence frames


@pytest.mark.parametrize("lang", ["ru", "kk", "en"])
def test_header_and_footer_on_every_page(pdfs: dict[str, bytes], lang: str) -> None:
    pages = [flat(p.extract_text()) for p in PdfReader(io.BytesIO(pdfs[lang])).pages]
    match = re.search(PAGE_ONE[lang], pages[0])
    assert match, pages[0]
    assert int(match.group(1)) == len(pages) >= 5
    assert LAST_PAGE[lang].format(n=len(pages)) in pages[-1]
    for page in pages:
        assert "QV-2026-0007" in page
        assert "Dana Serik" in page


def test_cover_page(pdfs: dict[str, bytes]) -> None:
    cover = flat(PdfReader(io.BytesIO(pdfs["en"])).pages[0].extract_text())
    labels = ["Inspection report", "Company", "Site", "Asset", "Capture date", "Report number"]
    for s in [*labels, "Version", "Caspian Inspection Services", "Vertical storage tank T-104"]:
        assert s in cover, s
    assert "Executive summary" not in cover


def test_findings_table_is_by_severity(pdfs: dict[str, bytes]) -> None:
    text = text_of(pdfs["en"])
    table = text[text.find("Findings by severity") : text.find("Finding details")]
    rows = re.findall(r"\b([345]) (?:Corrosion|Coating damage) (Critical|Watch)", table)
    assert rows == [("3", "Critical"), ("4", "Watch"), ("5", "Watch")]


def test_kazakh_letters_survive(pdfs: dict[str, bytes]) -> None:
    text = text_of(pdfs["kk"])
    for letter in "ҚӨҰҮӘІқңөұүәі":  # Ң never starts a Kazakh word, so only its small form
        assert letter in text, letter


def test_pdf_a(pdfs: dict[str, bytes]) -> None:
    reader = PdfReader(io.BytesIO(pdfs["en"]))
    root: Any = reader.trailer["/Root"]
    xmp = root["/Metadata"].get_object().get_data().decode()
    assert re.search(r"pdfaid:part(>|=\")3", xmp), xmp
    assert re.search(r"pdfaid:conformance(>|=\")B", xmp), xmp
    intent = root["/OutputIntents"][0].get_object()
    assert intent["/S"] == "/GTS_PDFA1"
    meta = reader.metadata
    assert meta is not None
    assert str(meta["/CreationDate"]).startswith("D:20261026143200+05")
    assert str(meta["/ModDate"]).startswith("D:20261026143200+05")


@pytest.mark.parametrize("lang", ["ru", "kk", "en"])
def test_every_font_is_embedded_plex_or_geologica(pdfs: dict[str, bytes], lang: str) -> None:
    names = set()
    for page in PdfReader(io.BytesIO(pdfs[lang])).pages:
        resources: Any = page["/Resources"]
        for ref in resources["/Font"].values():
            font: Any = ref.get_object()
            if font["/Subtype"] == "/Type0":
                font = font["/DescendantFonts"][0].get_object()
            desc = font["/FontDescriptor"].get_object()
            assert any(k in desc for k in ("/FontFile", "/FontFile2", "/FontFile3")), font
            names.add(str(font["/BaseFont"]))
    assert any("IBM-Plex-Sans" in n for n in names), names
    assert any("Geologica" in n for n in names), names
    assert all("Plex" in n or "Geologica" in n for n in names), names


def test_same_input_same_bytes(pdfs: dict[str, bytes]) -> None:
    assert render_report(INPUT, "en") == pdfs["en"]
    later = REPORT_TIME + timedelta(minutes=1)
    moved = ReportInput(**{**INPUT.__dict__, "report_time": later})
    assert render_report(moved, "en") != pdfs["en"]


def test_names_cannot_break_out_of_the_page_footer() -> None:
    """The engineer's name goes into a CSS string in a <style> element (the footer)."""
    name = 'Eve "\\ </style><h1>x</h1> }'
    hostile = ReportInput(**{**INPUT.__dict__, "engineer": name})
    pdf = render_report(hostile, "en")
    pages = [flat(p.extract_text()) for p in PdfReader(io.BytesIO(pdf)).pages]
    assert f"Engineer: {name}. Report time:" in pages[0]
    assert re.search(r"Page 1 of \d+", pages[0])


def test_translation_tables_have_the_same_keys() -> None:
    table = tomllib.loads((REPORT_DIR / "translations.toml").read_text())
    assert set(table) == {"ru", "kk", "en"}
    assert set(table["ru"]) == set(table["en"]) == set(table["kk"])

    def fields(v: Any) -> set[str]:
        if isinstance(v, list):
            return set().union(*(fields(x) for x in v))
        return {f for _, f, _, _ in string.Formatter().parse(v) if f}

    for key, en in table["en"].items():
        for lang in ("ru", "kk"):
            assert fields(table[lang][key]) == fields(en), (lang, key)
            assert type(table[lang][key]) is type(en), (lang, key)
            if isinstance(en, list):
                assert len(table[lang][key]) == len(en), (lang, key)
    assert all(len(table[lang]["months"]) == 12 for lang in table)


def test_template_has_no_visible_text_of_its_own() -> None:
    """Every translation replaced by a marker: what is left on the pages is markers and the
    fixture's own data (names, numbers, identifiers), nothing written in the template."""
    table = tomllib.loads((REPORT_DIR / "translations.toml").read_text())["en"]
    marked: dict[str, Any] = {}
    for i, (key, value) in enumerate(table.items()):
        marked[key] = [f"@k{i}x{j}@" for j in range(len(value))] if isinstance(value, list) else (
            f"@k{i}@")  # fmt: skip
    marked["decimal"] = "."
    text = text_of(render_pdf(INPUT, marked, "en"))
    assert marked["section_cards"] in text and marked["col_engineer"] in text
    rest = re.sub(r"@k\d+(x\d+)?@", " ", text)
    for data in ("Caspian Inspection Services", "Tank T-104", "Vertical storage tank T-104",
                 "Dana Serik", "Aslan Bekov", "manual: Letter sheet", "Shadow, not corrosion",
                 KK_COMMENT, "QV-2026-0007", "7F3A-09C2-B4E1-5D68"):  # fmt: skip
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
    inp = load_report_input(engine, 7, REPORT_TIME)
    assert (inp.organization, inp.site, inp.asset, inp.engineer) == (
        "Caspian Inspection Services", "Tank T-104", None, "Dana Serik",
    )  # fmt: skip
    created = datetime(2026, 10, 21, 4, 41, tzinfo=UTC)
    assert inp.inspection == InspectionInput(7, "quick_scan", created)
    assert inp.capture_date == created
    assert (inp.report_number, inp.version, inp.report_time) == ("QV-2026-0007", 1, REPORT_TIME)
    assert inp.versions == (VersionEntry(1, REPORT_TIME, "Dana Serik"),)
    assert re.fullmatch(r"[0-9A-F]{4}(-[0-9A-F]{4}){3}", inp.verification_id)
    assert load_report_input(engine, 7, REPORT_TIME).verification_id == inp.verification_id
    later = load_report_input(engine, 7, REPORT_TIME + timedelta(minutes=1))
    assert later.verification_id != inp.verification_id
    assert inp.coverage is None
    one, two = inp.findings
    assert (one.id, one.class_, one.severity, one.status) == (1, "corrosion", "watch", "confirmed")
    assert (one.area_m2, one.scale_source, one.area_uncertainty_pct) == (
        0.42, "manual: Letter sheet", None,
    )  # fmt: skip
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
    assert "масштаб орнатылмаған" in text and "Есеп QV-2026-0007, 1-нұсқа" in text
    assert "sha256" in capsys.readouterr().out


def test_cli_unknown_inspection_fails(tmp_path: Path) -> None:
    url = make_db(tmp_path / "api.db")
    with pytest.raises(SystemExit) as err:
        main(["report", "--inspection", "99", "--db", url, "--lang", "en",
              "--out", str(tmp_path / "x.pdf")])  # fmt: skip
    assert err.value.code == 1
    assert not (tmp_path / "x.pdf").exists()
