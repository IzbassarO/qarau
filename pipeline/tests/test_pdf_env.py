"""WeasyPrint finds Pango on every machine that runs the pipeline (owner 2026-10-27: RPT1 renders
the report from Jinja2 HTML with WeasyPrint): the Mac via Homebrew, CI and Docker via apt."""

import io

import pypdf

import qarau_pipeline  # noqa: F401  (sets the macOS library path before WeasyPrint loads)


def test_weasyprint_renders_cyrillic_and_kazakh():
    import weasyprint

    text = "Отчёт об осмотре. Қазақша: әғқңөұүһі"
    pdf = weasyprint.HTML(string=f"<p>{text}</p>").write_pdf()
    page = pypdf.PdfReader(io.BytesIO(pdf)).pages[0].extract_text()
    assert page.strip() == text
