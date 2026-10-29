"""PDF inspection report (RPT1, ADR 0027): ReportInput -> A4 PDF/A-3b, from the Jinja2 template
templates/report.html.j2 and templates/report.css, laid out by WeasyPrint (Pango from the system).

Sections in order: cover, executive summary, map, findings by severity, one card per finding,
not inspected areas, method and limitations, sign-off, document control; a header with the report
number and a footer with the engineer, the report time and "page X of Y" on every page. The map
snapshot and the evidence frames come from snapshot.py (ADR 0032), embedded as data URIs. Every
visible string comes from translations.toml; the v8 fonts are embedded from fonts/. The report
time inside the input is the only clock: the same input and language give the same bytes.
"""

from __future__ import annotations

import hashlib
import string
import tomllib
from datetime import datetime
from functools import cache
from pathlib import Path
from typing import Any, NamedTuple

import sqlalchemy as sa
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from markupsafe import Markup

from ..interfaces import StageError
from .inputs import DecisionEntry, FindingInput, ReportInput, load_report_input, with_coverage
from .snapshot import Plate, Visuals, load_visuals

HERE = Path(__file__).resolve().parent
TEMPLATES = HERE / "templates"
LANGS = ("ru", "kk", "en")
SEVERITY_ORDER = {"critical": 0, "watch": 1}
# SVG markers and outlines (v8 light `critical`, `watch`; `map-text` for a rejected outline)
COLOURS = {"critical": "#c42a18", "watch": "#a65400", "rejected": "#ededeb"}


def load_strings(lang: str) -> dict[str, Any]:
    with open(HERE / "translations.toml", "rb") as f:
        table = tomllib.load(f)
    if lang not in table:
        raise StageError(f"unknown report language {lang!r}; use one of {', '.join(LANGS)}")
    return dict(table[lang])


def fmt_time(t: datetime, s: dict[str, Any], tz: Any) -> str:
    """`date_format` of the language, in the report time's zone (documents: DESIGN.md)."""
    t = t.astimezone(tz)
    off = t.strftime("%z") or "+0000"
    return str(s["date_format"]).format(
        day=t.day, month=s["months"][t.month - 1], year=t.year, hm=t.strftime("%H:%M"),
        offset=f"{off[:3]}:{off[3:5]}",
    )  # fmt: skip


def fmt_number(x: float, s: dict[str, Any]) -> str:
    text = f"{x:.2f}" if x >= 0.995 else f"{x:.2g}"
    return text.replace(".", s["decimal"])


def fmt_area(m2: float, s: dict[str, Any]) -> str:
    return str(s["area_value"]).format(value=fmt_number(m2, s))


def css_content(template: str, **values: object) -> Markup:
    """A CSS `content` value from a translation: {page} and {pages} become page counters, every
    other field its value. Inside strings every character but letters, digits and spaces is a
    hex escape, so a name can never close the string or the <style> element. The space after
    each escape ends it and is consumed by the CSS parser."""

    def quoted(text: str) -> str:
        return '"' + "".join(c if c.isalnum() or c == " " else f"\\{ord(c):x} " for c in text) + '"'

    parts = []
    for literal, field, _, _ in string.Formatter().parse(template):
        if literal:
            parts.append(quoted(literal))
        if field in ("page", "pages"):
            parts.append(f"counter({field})")
        elif field is not None:
            parts.append(quoted(str(values[field])))
    return Markup(" ".join(parts) or '""')


@cache
def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(TEMPLATES), autoescape=True, undefined=StrictUndefined,
        trim_blocks=True, lstrip_blocks=True,
    )  # fmt: skip


def _context(inp: ReportInput, s: dict[str, Any], lang: str) -> dict[str, Any]:
    tz = inp.report_time.tzinfo

    def t(key: str, **kw: object) -> str:
        return str(s[key]).format(**kw)

    def word(prefix: str, value: str) -> str:
        return str(s.get(f"{prefix}_{value}", value))

    def when(time: datetime) -> str:
        return fmt_time(time, s, tz)

    def area(f: FindingInput) -> str:
        if f.area_m2 is None:
            return t("scale_not_set")
        value = fmt_area(f.area_m2, s)
        if f.area_uncertainty_pct is None:
            return t("area_no_uncertainty", area=value)
        pct = f"{f.area_uncertainty_pct:g}".replace(".", s["decimal"])
        return t("area_uncertain", area=value, value=pct)

    def verdict(d: DecisionEntry) -> str:
        text = word("verdict", d.verdict)
        if d.verdict == "reclass":
            new = [word("class", d.new_class) if d.new_class else None,
                   word("severity", d.new_severity) if d.new_severity else None]  # fmt: skip
            text += ": " + ", ".join(x for x in new if x)
        return text

    def decided(f: FindingInput) -> str:
        if not f.decisions:
            return t("undecided")
        last = f.decisions[-1]
        return t("decided", user=last.user, time=when(last.time))

    def plate_caption(p: Plate) -> str:
        if p.time_s is None:
            return t("evidence_frame", frame=p.frame_id)
        time = f"{p.time_s:.1f}".replace(".", s["decimal"])
        return t("plate_caption", frame=p.frame_id, time=time)

    def xyz(v: tuple[float, ...]) -> str:
        return "; ".join(f"{x:.2f}".replace(".", s["decimal"]) for x in v)

    findings = sorted(inp.findings, key=lambda f: (SEVERITY_ORDER.get(f.severity, 9), f.id))
    confirmed = [f for f in inp.findings if f.status == "confirmed"]
    measured = [f.area_m2 for f in confirmed if f.area_m2 is not None]
    summary = [
        (t("summary_total"), str(len(inp.findings))),
        (t("summary_critical"), str(sum(f.severity == "critical" for f in inp.findings))),
        (t("summary_watch"), str(sum(f.severity == "watch" for f in inp.findings))),
        (t("summary_confirmed"), str(len(confirmed))),
        (t("summary_rejected"), str(sum(f.status == "rejected" for f in inp.findings))),
        (t("summary_proposed"), str(sum(f.status == "proposed" for f in inp.findings))),
        (t("summary_area"), fmt_area(sum(measured), s) if measured else t("scale_not_set")),
        (t("summary_no_scale"), str(len(confirmed) - len(measured))),
        (
            t("summary_not_seen"),
            t("coverage_not_computed") if inp.coverage is None else str(len(inp.coverage.not_seen)),
        ),
    ]
    engineer = inp.engineer or t("engineer_none")
    return {
        "lang": lang, "s": s, "inp": inp, "t": t, "word": word, "when": when, "area": area,
        "verdict": verdict, "decided": decided, "plate_caption": plate_caption, "colours": COLOURS,
        "xyz": xyz,
        "findings": findings, "summary": summary,
        "engineer": engineer,
        "header": css_content(str(s["header"]), number=inp.report_number, version=inp.version),
        "footer": css_content(str(s["footer"]), engineer=engineer, time=when(inp.report_time)),
        "page_of": css_content(str(s["page_of"])),
    }  # fmt: skip


def render_html(
    inp: ReportInput, s: dict[str, Any], lang: str, visuals: Visuals | None = None
) -> str:
    """The report's HTML; `visuals` from snapshot.load_visuals (None: no runs dir)."""
    ctx = _context(inp, s, lang)
    ctx["visuals"] = visuals or load_visuals(inp, None)
    return _env().get_template("report.html.j2").render(ctx)


def render_pdf(
    inp: ReportInput, s: dict[str, Any], lang: str, visuals: Visuals | None = None
) -> bytes:
    """The report as PDF/A-3b bytes. `s` is one language table of translations.toml."""
    if inp.report_time.tzinfo is None:
        raise StageError("report time needs a time zone (ISO 8601 with an offset)")
    # Imported here: WeasyPrint loads Pango on import, and only the report needs it.
    from weasyprint import CSS, HTML
    from weasyprint.text.fonts import FontConfiguration

    html = render_html(inp, s, lang, visuals)
    fonts = FontConfiguration()
    css = CSS(filename=TEMPLATES / "report.css", font_config=fonts)
    pdf = HTML(string=html, base_url=str(TEMPLATES)).write_pdf(
        stylesheets=[css], font_config=fonts, pdf_variant="pdf/a-3b"
    )
    assert pdf is not None
    return pdf


def render_report(inp: ReportInput, lang: str, runs_dir: Path | None = None) -> bytes:
    """The PDF; the map, the evidence frames and the not-seen areas come from the run dirs
    under `runs_dir`."""
    inp = with_coverage(inp, runs_dir)
    return render_pdf(inp, load_strings(lang), lang, load_visuals(inp, runs_dir))


class Generated(NamedTuple):
    pdf: bytes
    sha256: str  # hex of `pdf`
    input: ReportInput  # what was rendered: report number, version, report time


def generate(
    inspection_id: int, lang: str, report_time: datetime, engine: sa.Engine,
    runs_dir: Path | None,
) -> Generated:  # fmt: skip
    """An inspection's report as PDF bytes with their SHA-256: the CLI and the API call this."""
    inp = load_report_input(engine, inspection_id, report_time)
    pdf = render_report(inp, lang, runs_dir)
    return Generated(pdf, hashlib.sha256(pdf).hexdigest(), inp)
