"""PDF inspection report (RPT1, ADR 0027): ReportInput -> A4 PDF, built with ReportLab platypus.

Sections in order: object, map, findings (each with its decision log), not-seen areas; a footer
with the engineer and the report time on every page. Every visible string comes from
translations.toml; the fonts (design system v8: Geologica, IBM Plex Sans) are
embedded from fonts/. ReportLab's invariant mode plus the report time as the PDF dates make the
output a pure function of (input, language, report time): the same three give the same bytes.
"""

from __future__ import annotations

import hashlib
import io
import tomllib
from datetime import datetime, tzinfo
from functools import cache
from html import escape
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (
    BaseDocTemplate,
    CondPageBreak,
    Flowable,
    Frame,
    KeepTogether,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from ..interfaces import StageError
from .inputs import FindingInput, ReportInput, load_report_input

HERE = Path(__file__).resolve().parent
LANGS = ("ru", "kk", "en")
# Design system v8, light theme (web/src/design/tokens.css): the report is always a white page.
INK, INK_MUTED, INK_FAINT = HexColor("#111111"), HexColor("#60605c"), HexColor("#8f8f8a")
LINE = HexColor("#e5e5e3")
SIGNAL = {"critical": HexColor("#c42a18"), "watch": HexColor("#a65400")}
FONTS = {
    "QvDisplay": "Geologica-SemiBold.ttf",
    "QvSans": "IBMPlexSans-Regular.ttf",
    "QvSans-Medium": "IBMPlexSans-Medium.ttf",
    "QvSans-SemiBold": "IBMPlexSans-SemiBold.ttf",
    "QvSans-Italic": "IBMPlexSans-Italic.ttf",
}
PX = 0.75  # the v8 report document is 794 px wide = A4 (595 pt)
MARGIN_X, MARGIN_TOP, MARGIN_BOTTOM = 72 * PX, 64 * PX, 88 * PX


def load_strings(lang: str) -> dict[str, Any]:
    with open(HERE / "translations.toml", "rb") as f:
        table = tomllib.load(f)
    if lang not in table:
        raise StageError(f"unknown report language {lang!r}; use one of {', '.join(LANGS)}")
    return dict(table[lang])


@cache
def _register_fonts() -> None:
    for name, file in FONTS.items():
        pdfmetrics.registerFont(TTFont(name, str(HERE / "fonts" / file)))
    pdfmetrics.registerFontFamily(
        "QvSans", normal="QvSans", bold="QvSans-SemiBold", italic="QvSans-Italic",
        boldItalic="QvSans-SemiBold",
    )  # fmt: skip


def _style(name: str, font: str, px: float, lead_px: float, **kw: Any) -> ParagraphStyle:
    # bulletFontName: ReportLab's default Helvetica would land in the PDF without being embedded
    kw.setdefault("textColor", INK)
    return ParagraphStyle(name, fontName=font, bulletFontName=font, fontSize=px * PX,
                          leading=lead_px * PX, **kw)  # fmt: skip


# Sizes from DESIGN.md (px): report title 30/36, h2 18/26, h3 16/22, body 14/22, tables 13.5/20.
TITLE = _style("title", "QvDisplay", 30, 36, spaceAfter=6 * PX)
SUBTITLE = _style("subtitle", "QvSans", 17, 24, textColor=INK_MUTED, spaceAfter=8 * PX)
H2 = _style("h2", "QvDisplay", 18, 26, spaceBefore=36 * PX, spaceAfter=12 * PX)
H3 = _style("h3", "QvDisplay", 16, 22, spaceBefore=22 * PX, spaceAfter=8 * PX)
H4 = _style("h4", "QvSans-SemiBold", 14, 20, spaceBefore=14 * PX, spaceAfter=4 * PX)
BODY = _style("body", "QvSans", 14, 22)
CELL = _style("cell", "QvSans", 13.5, 20)
LABEL = _style("label", "QvSans", 13.5, 20, textColor=INK_MUTED)
HEAD = _style("head", "QvSans-Medium", 13.5, 20, textColor=INK_MUTED)
BRAND = _style("brand", "QvSans-SemiBold", 16, 22)
REF = _style("ref", "QvSans", 13, 18, textColor=INK_MUTED, alignment=2)
NOTE = _style("note", "QvSans-Italic", 13, 19, textColor=INK_MUTED)


def fmt_time(t: datetime, s: dict[str, Any], tz: tzinfo | None) -> str:
    """`date_format` of the language, in the report time's zone (documents: DESIGN.md)."""
    t = t.astimezone(tz)
    off = t.strftime("%z") or "+0000"
    return str(s["date_format"]).format(
        day=t.day, month=s["months"][t.month - 1], year=t.year, hm=t.strftime("%H:%M"),
        offset=f"{off[:3]}:{off[3:5]}",
    )  # fmt: skip


def fmt_area(m2: float, s: dict[str, Any]) -> str:
    value = f"{m2:.2f}" if m2 >= 0.995 else f"{m2:.2g}"
    return str(s["area_value"]).format(value=value.replace(".", s["decimal"]))


def _p(text: str, style: ParagraphStyle) -> Paragraph:
    """Paragraph from plain text (escaped: names and comments are user input)."""
    return Paragraph(escape(text), style)


def _heading(n: int, text: str) -> Paragraph:
    return Paragraph(f'<font color="{INK_FAINT.hexval()}">{n}</font>&nbsp;&nbsp;{escape(text)}', H2)


def _table(rows: list[list[Flowable]], widths: list[float], *style: tuple[Any, ...],
           repeat: int = 0) -> Table:  # fmt: skip
    t = Table(rows, colWidths=widths, repeatRows=repeat, hAlign="LEFT")
    # FONT: a table sets its cell font even around Paragraphs; its default is Helvetica, which
    # would land in the PDF without being embedded.
    base = [("FONT", (0, 0), (-1, -1), "QvSans"), ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0)]  # fmt: skip
    t.setStyle(TableStyle([*base, *style]))
    return t


def _facts(rows: list[tuple[str, Flowable | str]], width: float) -> Table:
    """Two-column label/value table (`qv-facts`)."""
    data = [[_p(k, LABEL), v if isinstance(v, Flowable) else _p(v, CELL)] for k, v in rows]
    return _table(
        data, [width * 0.3, width * 0.7],
        ("TOPPADDING", (0, 0), (-1, -1), 5 * PX),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5 * PX),
        ("LINEBELOW", (0, 0), (-1, -1), 0.5, LINE),
    )  # fmt: skip


def _decision_log(f: FindingInput, s: dict[str, Any], tz: tzinfo | None, width: float) -> Flowable:
    if not f.decisions:
        return _p(s["decisions_none"], CELL)
    head = [_p(s[k], HEAD) for k in ("col_time", "col_engineer", "col_verdict", "col_comment")]
    rows: list[list[Flowable]] = [head]
    for d in f.decisions:
        verdict = str(s[f"verdict_{d.verdict}"])
        if d.verdict == "reclass":
            new = [s.get(f"class_{d.new_class}", d.new_class) if d.new_class else None,
                   s[f"severity_{d.new_severity}"] if d.new_severity else None]  # fmt: skip
            verdict += ": " + ", ".join(str(x) for x in new if x)
        rows.append([_p(fmt_time(d.time, s, tz), CELL), _p(d.user, CELL), _p(verdict, CELL),
                     _p(d.comment or "", CELL)])  # fmt: skip
    return _table(
        rows, [w * width for w in (0.25, 0.17, 0.27, 0.31)],
        ("RIGHTPADDING", (0, 0), (-1, -1), 10 * PX),
        ("TOPPADDING", (0, 0), (-1, -1), 6 * PX),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6 * PX),
        ("LINEBELOW", (0, 0), (-1, 0), 0.75, INK),
        ("LINEBELOW", (0, 1), (-1, -1), 0.5, LINE),
        repeat=1,
    )  # fmt: skip


def _finding(f: FindingInput, s: dict[str, Any], tz: tzinfo | None, width: float) -> list[Any]:
    colour = SIGNAL.get(f.severity, INK).hexval()
    severity = Paragraph(
        f'<font color="{colour}">{escape(str(s[f"severity_{f.severity}"]))}</font>', CELL
    )
    area = fmt_area(f.area_m2, s) if f.area_m2 is not None else s["area_not_measured"]
    frames = ", ".join(str(s["evidence_frame"]).format(frame=e.frame_id) for e in f.evidence)
    facts = _facts([
        (s["label_class"], str(s.get(f"class_{f.class_}", f.class_))),
        (s["label_severity"], severity),
        (s["label_status"], str(s.get(f"status_{f.status}", f.status))),
        (s["label_area"], area),
        (s["label_scale"], f.scale_source or s["scale_not_set"]),
        (s["label_evidence"], frames or s["evidence_none"]),
    ], width)  # fmt: skip
    title = _p(str(s["finding_title"]).format(id=f.id), H3)
    return [KeepTogether([title, facts]), _p(s["decision_log"], H4),
            _decision_log(f, s, tz, width)]  # fmt: skip


def render_pdf(inp: ReportInput, s: dict[str, Any], report_time: datetime) -> bytes:
    """The report as PDF bytes. `s` is one language table of translations.toml."""
    if report_time.tzinfo is None:
        raise StageError("report time needs a time zone (ISO 8601 with an offset)")
    _register_fonts()
    tz = report_time.tzinfo
    when = fmt_time(report_time, s, tz)
    engineer = inp.engineer or str(s["engineer_none"])
    width = A4[0] - 2 * MARGIN_X
    off = report_time.strftime("%z")
    pdf_date = f"D:{report_time:%Y%m%d%H%M%S}{off[:3]}'{off[3:5]}'"
    # The file ID comes from the inputs, so two different reports never share one.
    fingerprint = hashlib.sha256(repr((inp, s, report_time.isoformat())).encode()).digest()

    def on_page(canvas: Canvas, doc: BaseDocTemplate) -> None:
        canvas.setDateFormatter(lambda *_: pdf_date)  # CreationDate and ModDate
        if doc.page == 1:
            canvas._doc.updateSignature(fingerprint)
        canvas.saveState()
        y = MARGIN_BOTTOM - 40 * PX
        canvas.setStrokeColor(LINE)
        canvas.setLineWidth(0.5)
        canvas.line(MARGIN_X, y + 14 * PX, A4[0] - MARGIN_X, y + 14 * PX)
        canvas.setFont("QvSans", 13 * PX)
        canvas.setFillColor(INK_MUTED)
        canvas.drawString(MARGIN_X, y, str(s["footer"]).format(engineer=engineer, time=when))
        canvas.drawRightString(A4[0] - MARGIN_X, y, str(s["page"]).format(n=doc.page))
        canvas.restoreState()

    insp = inp.inspection
    band = _table(
        [[_p(inp.organization, BRAND), _p(when, REF)]], [width * 0.6, width * 0.4],
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 14 * PX),
        ("LINEBELOW", (0, 0), (-1, -1), 1.5, INK),
    )  # fmt: skip
    mode = s[f"mode_{insp.mode}"]
    story: list[Any] = [
        band,
        Spacer(1, 28 * PX),
        _p(s["doc_title"], TITLE),
        _p(inp.site, SUBTITLE),
        _heading(1, s["section_object"]),
        _facts(
            [
                (s["label_organization"], inp.organization),
                (s["label_site"], inp.site),
                (s["label_inspection"], str(s["inspection_ref"]).format(id=insp.id, mode=mode)),
                (s["label_inspection_date"], fmt_time(insp.date, s, tz)),
                (s["label_engineer"], engineer),
                (s["label_report_time"], when),
            ],
            width,
        ),  # fmt: skip
        _heading(2, s["section_map"]),
        _p(s["map_pending"], NOTE),
        CondPageBreak(120 * PX),
        _heading(3, s["section_findings"]),
    ]
    if not inp.findings:
        story.append(_p(s["findings_none"], BODY))
    for f in inp.findings:
        story += _finding(f, s, tz, width)
    story += [CondPageBreak(80 * PX), _heading(4, s["section_not_seen"])]
    # ponytail: COV1 replaces this with the list of not-seen areas once coverage is computed
    story.append(_p(s["coverage_not_computed"], BODY))

    buf = io.BytesIO()
    doc = BaseDocTemplate(
        buf, pagesize=A4, invariant=1, initialFontName="QvSans",
        title=f"{s['doc_title']}: {inp.site}", author=engineer, subject=inp.organization,
        creator=s["product"], producer=s["product"],
    )  # fmt: skip
    body = Frame(MARGIN_X, MARGIN_BOTTOM, width, A4[1] - MARGIN_TOP - MARGIN_BOTTOM,
                 leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)  # fmt: skip
    # The footer is drawn at the end of each page, so it also reads after the page's content.
    doc.addPageTemplates([PageTemplate("page", [body], onPageEnd=on_page)])
    doc.build(story)
    return buf.getvalue()


def generate(
    inspection_id: int, lang: str, report_time: datetime, engine: sa.Engine
) -> tuple[bytes, str]:
    """(PDF bytes, their SHA-256 hex) of an inspection's report: the CLI and the API call this."""
    pdf = render_pdf(load_report_input(engine, inspection_id), load_strings(lang), report_time)
    return pdf, hashlib.sha256(pdf).hexdigest()
