# 0027. PDF inspection report: Jinja2 + WeasyPrint, PDF/A-3b, embedded v8 fonts, deterministic bytes

Date: 2026-10-27. Status: accepted (RPT1 part 1). Replaces the ReportLab draft of 2026-10-26.

## Context

RPT1 needs a PDF report per inspection in ru, kk and en, built by the same function for the CLI
and for `GET /api/inspections/{id}/report.pdf`. Its SHA-256 is stored, so the same inputs must
give the same bytes. It runs in the worker/API images and on customers' servers without
internet. The owner (2026-10-27) made it a corporate document for company managers (cover,
one-page executive summary, findings table by severity, one card per finding, not inspected
areas, method and limitations, sign-off, document control, header/footer with the report number
and "page X of Y", PDF/A) and required WeasyPrint + Jinja2, with Pango from the system: Homebrew
on the Mac, apt in CI and `infra/worker`.

## Decision

- **Jinja2 HTML → WeasyPrint.** `report/templates/report.html.j2` (autoescaped, strict
  undefined) and `report/templates/report.css`; `report/render.py` builds the template context
  and calls `HTML.write_pdf`. Licences: WeasyPrint BSD-3-Clause, Jinja2 BSD-3-Clause, Pango
  LGPL-2.1+ (system library, dynamically linked), pyphen LGPL-2.1+/MPL-1.1 (owner OK
  2026-10-27). No GPL tool (Ghostscript etc.) is in the path. ReportLab is dropped.
- **Page header and footer** are CSS `@page` margin boxes: the report number and version top
  right, the engineer and report time bottom left, "page X of Y" bottom right with
  `counter(page)` and `counter(pages)`. Their text is a translation turned into a CSS `content`
  value (`css_content`): `{page}`/`{pages}` become counters, so each language orders the words
  its own way (kk: `7 беттің 1-беті`). Every character of a string other than a letter, digit
  or space becomes a CSS hex escape, so a user's name can never close the string or the
  `<style>` element (tested with a hostile name).
- **Strings.** Every visible string is in `report/translations.toml` (ru/kk/en, same keys and
  same `{fields}`, tested). A test renders the template with every translation replaced by a
  marker and checks that only markers and the input's own data remain on the pages.
- **Design system v8.** Colours are the light-theme tokens (the report is always a white
  document), sizes and rules come from the `qv-doc` block of `web/src/design/bundle.css`.
  Fonts: Geologica SemiBold for headings, IBM Plex Sans Regular/Medium/SemiBold/Italic for text,
  loaded by `@font-face` from `report/fonts/` (TrueType merged from the latin, latin-ext,
  cyrillic and cyrillic-ext subsets, SIL OFL 1.1, `OFL.txt`), so Kazakh letters render and no
  system font is needed. WeasyPrint embeds a subset of each; a test checks that every font in
  the file is embedded and is Plex or Geologica. v8 retired IBM Plex Mono and sets identifiers
  in JetBrains Mono; the report sets its two identifiers (report number, verification ID) in
  Plex Sans with tabular figures instead of adding a fifth font file.
- **PDF/A-3b** (`pdf_variant="pdf/a-3b"`): XMP metadata with `pdfaid`, an sRGB output intent,
  all fonts embedded. Checked by tests; a full veraPDF validation is not part of CI.
- **Determinism.** `render_report(input, lang)` is pure: the report time is a field of
  `ReportInput` and the only clock in the file. It is written as `dcterms.created` and
  `dcterms.modified` (the PDF CreationDate/ModDate and XMP dates). WeasyPrint's file ID is a hash
  of the content, and its font subset tags are stable. Checked: two renders in one process and
  in two processes give the same SHA-256. The bytes also depend on the WeasyPrint and Pango
  versions, so a library upgrade changes the hash of a re-rendered report; the stored hash
  (part 3) is for the file that was issued.
- **Inputs.** `ReportInput` carries organization, site, asset, inspection, capture date, report
  number, version, engineer, report time, findings (with evidence, decisions, area, scale source
  and area uncertainty), version history and verification ID. `load_report_input` reads them
  from the API tables with SQLAlchemy Core. Until the Report table (part 3) numbers reports, the
  number is `QV-<year>-<inspection id>`, version 1; the capture date is the inspection's date;
  the asset is "not recorded" (no Asset table yet); the area uncertainty is "not estimated"
  (no column holds one). The verification ID is the first 64 bits of a SHA-256 of the report's
  content, shown as four groups of four hex digits. The finding's status, class and severity
  are replayed from its decision log by `replay_decisions`, which `backend/app/decisions.py`
  calls too.
- **No invented numbers.** An area in m² only with a scale source, otherwise "scale not set";
  "± X %" only when the data carries an uncertainty, otherwise "uncertainty not estimated"; no
  confidence values anywhere.

## Consequences

- WeasyPrint needs Pango at run time. On the Mac, `qarau_pipeline/__init__.py` points
  `DYLD_FALLBACK_LIBRARY_PATH` at Homebrew; CI and the worker image install it with apt.
- Layout changes are HTML/CSS. A render takes about 1 s for the 7-page fixture.
- Next: the map snapshot and the 3 evidence frames with SVG outlines (part 2, own ADR), the route,
  the Report table and the hash headers (part 3). Not in RPT1: XLSX, work orders, e-mail,
  signatures, coverage computation (COV1 fills the "not inspected" section).
