# Design

Reference screens are in `prototypes/` (copied from the Claude Design canvas
"Qarau Vision — product screens"). They are self-contained `.dc.html` mockups: read the
markup for layout and copy, and the `<script type="text/x-dc">` block for interaction
logic. They load a design-tool runtime (`support.js`) that is not in this repo, so they
are a reference to read, not pages to serve.

| Prototype | Screen |
| --- | --- |
| `Viewer3D.dc.html` | **3D viewer + video sync** — the core screen; spec in `viewer-3d.md` |
| `Main.dc.html` | Inspection overview: layers, legend, coverage, findings list |
| `Finding.dc.html` | Finding evidence (frames, mask, history) and engineer decision |
| `Inspections.dc.html` | Site page: upload, mode choice, processing stages, inspections table |
| `Landing.dc.html` | Public website |

Build the web app to match them.

## Principles

- The product is the data, not the decoration. Neutral UI; colour only carries meaning.
- Every AI output is shown with its evidence and an explicit "check this" label.
- Not-seen areas are always visible (gray) and always listed.
- Restrained, corporate, close to Claude / ChatGPT product UI: flat surfaces, thin
  borders, no gradients, no emoji, no shadows-as-decoration.

## Tokens

| Token | App (dark) | Website (light) | Use |
| --- | --- | --- | --- |
| bg | `#0B0C0E` | `#FFFFFF` | page |
| surface | `#0E1013` | `#F5F6F7` | side panels, alt sections |
| surface-raised | `#181B20` | `#FFFFFF` | selected rows, pressed toggles |
| border | `#23272D` | `#E3E5E8` | dividers, cards |
| border-strong | `#2F3540` | `#C9CDD3` | buttons, inputs |
| ink | `#E8EAED` | `#111317` | primary text, primary button fill |
| ink-2 | `#A1A7B0` | `#4F5660` | secondary text |
| link | `#8AB6FF` | `#1F5FD1` | links |
| viewer-bg | `#000000` | — | 3D viewer |
| critical | `#F0565C` (text `#FF8A8E`) | | critical findings |
| watch | `#F2A33A` (text `#F6BE6E`) | | findings to watch, warnings |
| inspected | `#5B9BFF` | | inspected OK, progress, focus |
| not-seen | `#6B727D` | | not in any usable frame |

Point height ramp (low → high): `#0E2A5C` → `#2E6FD8` → `#9CC3FF` → `#EAF2FF`.

## Type

- IBM Plex Sans 400/500/600 for UI; IBM Plex Mono 400/500 for numbers, timestamps,
  coordinates, measurements.
- UI 14/20, secondary 13/19, labels 12/18 uppercase +0.06em, page titles 24/32 600.
- Website hero 52/60 600, section titles 32/40 600, body 16/26.

## Shape

Radius 8 (buttons, inputs), 10–12 (cards, viewer), 999 (pills, markers). Buttons 36–48 px
high. Primary button: ink fill with bg-coloured text.
