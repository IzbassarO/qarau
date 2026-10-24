# Qarau design rules (v8)

Rules for every visual change in `web/` (and the Tauri shell around it). They come first; the
frontend-design skill (`.claude/skills/frontend-design/SKILL.md`, Anthropic, Apache-2.0) is the
general method. Where the two disagree, these rules win: the skill itself says the brief's own
words win. Where this file and `CLAUDE.md` (trust rules) disagree, `CLAUDE.md` wins; the known
conflicts are listed under "Open decisions" at the end.

Source: the owner's design system v8 from Claude Design (2026-10-23), merged with the v1 rules
(owner brief, 2026-10-19) that still hold. Files:

| What | Where | Status |
| --- | --- | --- |
| Rules, lists, checklist | this file | current |
| Token values (JSON, platform-neutral) | `web/src/design/tokens.json` | `light` / `dark` / `shared` = what the app renders today (ADR 0018); `v8` = the new values, not wired yet |
| v8 tokens as CSS custom properties, three themes | `web/src/design/tokens.css` | landed, not imported |
| v8 component styles, prefix `qv-` | `web/src/design/bundle.css` | landed, not imported |
| Reference screens | `docs/design/screens/<screen>-light.png` | inspections, viewer, finding, report, sign-in, upload, states, themes. Each file shows the light theme on top and the dark theme below it |
| The 3D viewer behaviour | `docs/design/viewer-3d.md` | current |

Task DS1 wires `tokens.css` and `bundle.css` into `web/src/index.css` and retires the v1 values.
Until then no screen changes.

## What it is

Qarau Vision turns a phone video of a tank wall or a pipe rack into a measured 3D map with
corrosion marked on it, evidence frames for every finding, an append-only confirm/reject record
and a signed PDF report. The user is an inspection engineer at a contractor in Kazakhstan, on a
laptop next to the equipment, and wants one answer: **where are the defects, how big, show me the
proof.** The same tokens and components serve the web app, the desktop app, the client's
read-only phone view and the report.

An enterprise industrial inspection tool, not an AI demo. Calm, dense and readable: engineers
check findings for hours. The 3D viewer is the hero of the product; everything else is quiet.

## Rules

1. One primary action per screen (`qv-btn`, the accent). Everything else is `qv-btn--secondary`
   or `qv-btn--quiet`.
2. Every number carries a unit, and the report says once how accurate areas are (about 2 % with
   a Letter sheet). No formulas in the product. Model confidence stays on the evidence panel
   (`CLAUDE.md` trust rules; see "Open decisions").
3. Every finding shows frames with the mask outlined before it asks for a decision.
4. Decisions are appended, never edited. Signing a report locks them; a new review is a new
   revision.
5. Colour means something or it is gray. The accent is the one brand colour; the signal colours
   each mean one thing.
6. Layers, not lines: surfaces sit on `bg` with `line` and `shadow-sm`; panels and dialogs lift
   with `shadow-md` and `shadow-lg`. No gradients in the app (the sign-in hero is the one
   exception), no tinted backgrounds, no decoration.
7. Say what happened and the one thing to do, in plain sentences.
8. Write like a person, not a template: no uppercase labels, no strings joined with middle dots,
   no "Word — fragment" captions, no monospace for ordinary numbers, no dots in front of status
   words. Commas and full stops, dates as "21 Oct, 10:12", counts as "2 of 4 reviewed".
9. The map is the screen, in two views: orbit (drag to turn, scroll to zoom without limit) and
   walk (keys to move, mouse to look, Esc for the menu). Everything else is a pill that opens on
   click; the finding card opens on click; few buttons, in plain words.
10. Spend boldness in one place per screen (usually the data), keep the rest quiet.

## Writing

- English first; Russian runs about 30 % longer and Kazakh similar, so a label must survive
  +30 % without wrapping or clipping. Rail toggles and chips are one word. All text goes through
  i18n (en/ru/kk), never hard-coded.
- Sentence case. Verbs on buttons (`Confirm`, `Upload and process`, `Sign and send`). Never
  `Submit`, `OK`, `Success`.
- Units: SI with a space before the unit, `±` spaced. Dates `21 October 2026 at 14:32` in
  documents, `21 Oct, 10:12` in the app, `Today, 09:41` in lists. Frames `Frame 412 at 27.4 s`,
  percentages `94 %`.
- Explain a number with a comparison a person can picture (`about a chair seat`) rather than a
  tolerance.
- State copy lives in the States screen (`screens/states-light.png`); reuse it word for word.
- No marketing copy, no developer or debug text, no emoji.

## Colour

| token | role |
| --- | --- |
| `bg`, `surface`, `surface-raised`, `surface-sunken`, `selection` | the four layers and the selected state. Neutral: soft white in Light, charcoal in Dark, near-black with a cool cast in Midnight |
| `line`, `line-strong` | hairlines; borders that must be found by eye |
| `ink`, `ink-muted`, `ink-faint` | text, secondary text, placeholders. `ink-faint` is below 4.5:1 by design: placeholders and disabled labels only, never for anything the engineer must read |
| `accent`, `accent-hover`, `accent-soft`, `on-accent` | ink itself: black on light, white on dark. Primary buttons, focus rings, links, the active mode. The chrome has no hue, so the signals read instantly |
| `critical` | a critical finding: map region, mask, status word, count. Red |
| `watch` | a finding to watch; needs re-shoot; an estimated number. Orange |
| `ok` | green: a passed check, a sent report, inspected and clean in lists and statuses |
| `unseen` | surface the camera did not see. Gray |
| `*-soft` | a wash behind a chip in the report summary only |
| `map-ground`, `map-grid`, `map-text` | the 3D map in every theme |

**Product colours are reserved** (`CLAUDE.md`): red = finding `critical`, orange = `watch`,
gray = `not_seen`, base blue = `inspected_ok` on the map. Never use them for decoration, for
errors or for anything else, so a red pixel always means a critical finding. Never a red button,
a coloured page background or a badge for decoration. The report is always a white document, in
every app theme.

## Themes

Three themes, Light, Dark and Midnight, from the same tokens (`[data-theme]` in `tokens.css`);
every signal colour has a value per theme and stays at 4.5:1 or better on `surface`. Light is as
complete as Dark. The theme follows `prefers-color-scheme` until a manual switch exists (a later
feature; Midnight is reachable only through it). Contrast is at least WCAG AA in every theme;
keyboard focus is always visible (`shadow-focus`); respect `prefers-reduced-motion`.

The 3D viewer canvas keeps one colour in every theme; only the chrome around it changes theme.

## The mark

A Q drawn as eight points on a ring, with the tail as a ninth, red point: the point cloud, the
name, and a finding, in one shape. On a black tile in the app (`qv-mark--tile`), on a white tile
on the dark sign-in panel, bare in the report band. Never recoloured, never with a gradient.

## Type

The interface runs in Russian, Kazakh and English, so every family covers Latin, Cyrillic and
Cyrillic extended (Ә Ғ Қ Ң Ө Ұ Ү Һ І set in the same face as the Latin). Four families with a
job each:

- **Geologica** (semibold, 600) for everything that names a place: the hero line, page and card
  titles, report headings, the brand.
- **IBM Plex Sans** for everything readable, in regular, medium (buttons, labels), semibold
  (names, verdicts) and a true italic: italic is for asides next to a number (`about a chair
  seat`), figure captions and log notes; underline only on links.
- **Lora** italic for one sentence of voice, the tagline.
- **JetBrains Mono** for identifiers and the open finding's area only. Tabular figures for
  numbers in tables and stats.

All four are on Google Fonts (OFL) with the `cyrillic-ext` subset (checked 2026-10-24). They are
**self-hosted** through `@fontsource` packages: on-premise installs have no internet, so never
load fonts from a CDN.

Sizes: body 14/20, captions and labels 13/18, headings 16/22, titles 22/28, the report title
30/36, the hero 44/48. Nothing readable below 13 px.

## Layout

4 px grid: 4, 8, 12, 16, 20, 24, 32, 40, 48. Laptop 1280 first: sidebar 248 (collapsible from
its header), top bar 48, rows 56, rails 40, side panel 460, timeline 108, dialogs 640 to 920,
report document 794 (A4 at 96 dpi). Radii 6, 8, 12 and 16: the same radius for the same level
of the layout, not one radius everywhere. Phone portrait (390) is read-only.

Lists and tables show many rows without scrolling the whole page; left-aligned text, numbers
right-aligned in columns; the run list shows name and date clearly, newest first.

## Components

`web/src/design/bundle.css`, prefix `qv-`, every value from the tokens. Button, ListRow (+ chips,
progress), StatusText, Toggle (switch, rail, checklist, inputs), FrameThumbnail, SidePanel,
DecisionLogEntry. App shell: `qv-app` with `qv-side` and `qv-main`; `qv-screen` for the
full-bleed viewer and report; `qv-float` and `qv-fold` for panels over the map; `qv-pop` for the
point popover; `qv-detail` and `qv-assist` for the finding card; `qv-doc` for the report
document; `qv-send` for the sign-and-send sheet; `qv-hero` for the sign-in panel.

Components never use literal colours or sizes; `noLiterals.test.ts` enforces it for the shell
and auth code. `bundle.css` as landed still carries a few `#fff` / `#111` literals, literal
font sizes (12.5, 13.5, 15 px) and the sign-in gradient; DS1 replaces them with tokens when it
wires the file in.

## Forbidden

- A colour or length literal in a component; a token used for a meaning it does not have.
- Red, orange, gray or blue for anything but critical, watch, not seen, inspected OK.
- Gradients (except the sign-in hero), glow, glassmorphism, backdrop blur, text shadows,
  tinted page backgrounds, decorative badges, emoji, AI-sparkle icons.
- Uppercase labels, middle-dot joins, "Word — fragment" captions, monospace for ordinary
  numbers, `Submit` / `OK` / `Success`.
- A number without a unit; readable text below 13 px; contrast under 4.5:1 for anything the
  engineer must read.
- Fonts from a CDN; a family without Cyrillic extended.
- Hard-coded strings in any language; a layout that clips a +30 % Russian or Kazakh label.
- A second primary button on one screen; a red button.
- Presenting `not_seen` as OK; a finding without its evidence frames.

## Required

- Every value from `tokens.json` through the CSS custom properties; light, dark (and midnight
  once switchable) from the same tokens.
- One primary action per screen; verbs on buttons; sentence case.
- Every finding: frames with the mask outlined, measured size with unit, the status word in its
  signal colour, confirm / reject, and the append-only log.
- Coverage shown: not-seen regions gray on the map and listed in the report.
- Visible keyboard focus on every control; `prefers-reduced-motion` respected.
- Russian, Kazakh and English through i18n; state copy reused word for word from States.
- Before/after screenshots of every changed page at 1440x900, light and dark (see Process).

## Reviewer checklist (yes/no)

1. Does every colour, length, radius, shadow and font come from a token (no literals)?
2. Is there exactly one primary button on the screen?
3. Do red, orange, gray and blue appear only for critical, watch, not seen and inspected OK?
4. Does every number carry a unit, and is nothing readable below 13 px?
5. Is every text the engineer must read at 4.5:1 or better, in light and in dark?
6. Are all strings in i18n (en/ru/kk) and do the Russian and Kazakh versions fit without
   clipping?
7. Are labels sentence case, buttons verbs, dates in the app format (`21 Oct, 10:12`)?
8. Does every finding show frames with the mask before asking for a decision, and is the
   decision appended, never edited?
9. Is keyboard focus visible on every control and is motion off under `prefers-reduced-motion`?
10. Are there no gradients, glow, blur, tinted backgrounds, emoji or debug text, and is the
    viewer canvas unchanged in both themes?

## Process

- No new features or behaviour changes in a design pass: the same pages, the same data, the
  same tests (updated only where the markup changed).
- Before/after screenshots of each changed page come from the tester (`dev-*` = before), at
  1440x900. Mobile-width screenshots come with a later Kerege change (multi-viewport
  screenshots).
- The reference screens in `screens/` are the target; a PR that restyles a page links the
  matching screen in its description.

## Open decisions (owner)

v8 and `CLAUDE.md` disagree in four places. Until the owner decides, `CLAUDE.md` holds.

1. **Inspected OK colour.** `CLAUDE.md`: base blue on the map. v8 defines no blue; `ok` is
   green ("inspected and clean"). Today the viewer keeps `finding-inspected` blue (#5b9bff).
2. **Model confidence.** `CLAUDE.md` trust rules show confidence with every finding; v8 rule 2
   says "no scores, confidence values or formulas anywhere". Today confidence stays on the
   evidence panel.
3. **Viewer canvas.** `CLAUDE.md` and ADR 0018: black (#000000) in both themes; v8
   `map-ground` is #131313. `tokens.test.ts` asserts #000000.
4. **Sign-in gradient.** v8 allows one gradient on the sign-in hero; `noLiterals.test.ts`
   rejects gradients in `src/auth`. Either the test gets an exception for the hero or the hero
   loses the gradient.
5. **Non-text contrast.** v8 says `line-strong` is "≥ 3:1 on surface", but it measures 1.51
   (light), 2.14 (dark) and 1.89 (midnight) on `bg`. The `shadow-focus` halo measures 1.48 to
   2.16 against `surface`. WCAG 1.4.11 asks 3:1 for input borders and focus indicators; the v1
   values met it (`border-strong` 3.39 / 4.66, focus ring 6.52 / 6.94), and `tokens.test.ts`
   checks it. DS1 needs either darker v8 values or a solid 2 px `accent` focus outline.

Two defects in the v8 export itself, for DS1 to fix when wiring `tokens.css`:

- `.t-body-italic` and `.t-quote` lack `font-style: italic`, although `tokens.json` sets it.
- Dark and Midnight apply only through `[data-theme]`; there is no
  `prefers-color-scheme: dark` block, so the app must set `data-theme` itself to follow the
  system.
