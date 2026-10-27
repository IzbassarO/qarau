# 0027. PDF inspection report: ReportLab, embedded v8 fonts, deterministic bytes

Date: 2026-10-26. Status: accepted (RPT1 part 1).

## Context

RPT1 needs a PDF report per inspection (object, map, findings with evidence and decision log,
not-seen areas, footer) in ru, kk and en, built by the same function for the CLI and for
`GET /api/inspections/{id}/report.pdf`. Its SHA-256 is stored, so the same inputs must give the
same bytes. It runs in the worker/API images and on customers' servers without internet.

The task allowed WeasyPrint (BSD-3-Clause, HTML template → PDF) or ReportLab (BSD). The plan
picked WeasyPrint. WeasyPrint loads the system Pango, cairo/HarfBuzz and GObject libraries at
run time. They are not on the dev Mac, and installing system packages (`brew install pango`)
is outside what an agent may do here. Tests that cannot run locally cannot gate a merge.

## Decision

- **ReportLab** (BSD-3-Clause, `reportlab>=4.2`, locked 5.1.0) with platypus flowables. It is
  a plain wheel with no system libraries, so no Dockerfile change, no LGPL system-library
  notices on on-prem images, and it runs the same on macOS, CI and Linux images.
- **No HTML template.** The document is built in `report/render.py` (about 150 lines of
  flowables). Every visible string comes from `report/translations.toml`. A test swaps every
  translation for a marker and checks that the page holds nothing but markers and the input's
  own data, so no text is hard-coded. The section order lives in one function.
- **Fonts: design system v8**, not the Plex Sans/Mono from the plan (v8 retired Plex Mono).
  Geologica SemiBold for headings, IBM Plex Sans Regular/Medium/SemiBold/Italic for text.
  These are the same Google Fonts files the web app self-hosts through `@fontsource`. ReportLab
  needs one TrueType file per weight, so the latin, latin-ext, cyrillic and cyrillic-ext WOFF
  subsets of each weight were merged once with fontTools (MIT, used once to make the files,
  not a dependency) and vendored in `report/fonts/` with `OFL.txt` (SIL OFL 1.1, all
  families). Kazakh letters (Ә Ғ Қ Ң Ө Ұ Ү Һ І) are covered. ReportLab embeds a subset of
  each font. Two of its Helvetica defaults are overridden, because Helvetica would be
  referenced without being embedded: a table's cell font and a paragraph's bullet font.
- **Determinism.** `invariant=1` (no wall clock, fixed object order). The PDF CreationDate and
  ModDate are set to the report time. The file ID is ReportLab's digest plus a SHA-256 of
  (input, strings, report time), so two different reports never share an ID. Times in the PDF
  are shown in the report time's UTC offset. The report time is an input (CLI
  `--report-time`, default now) and is the only clock value in the file. Checked: two CLI runs
  in separate processes give the same SHA-256.
- **One reader.** `report/inputs.py` reads the API tables with SQLAlchemy Core (column lists
  only; the schema stays in backend Alembic migrations). The finding's effective status, class
  and severity come from `replay_decisions`, which `backend/app/decisions.py` now calls too.

## Consequences

- The pipeline depends on reportlab (BSD; it pulls Pillow, MIT-CMU, and charset-normalizer,
  MIT), SQLAlchemy (MIT) and psycopg (LGPL-3.0, unmodified, already a backend dependency) for
  the CLI's Postgres access. Tests read the PDFs with pypdf (BSD-3-Clause, dev only).
- Layout changes are Python, not CSS. If the report later needs rich HTML/CSS layout, WeasyPrint
  can replace `render.py` behind the same `generate()` once Pango is in the dev and CI setup.
- Not in this ADR: the map snapshot and evidence images (RPT1 part 2, own ADR), the Report table
  and the route (part 3), XLSX, work orders and signatures.
