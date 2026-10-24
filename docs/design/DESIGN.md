# Qarau design rules (v8)

**Design system v8 is the source of truth for the product UI** (owner, 2026-10-24): this file,
the reference screens in `docs/design/screens/`, `web/src/design/tokens.*` and
`web/src/design/bundle.css`. It is the only visual source; `CLAUDE.md` points here.

- Agents never invent or change colours, fonts, components or screen layout.
- Allowed adjustments: alignment, spacing, sizes and responsive behaviour, so a screen fits real
  content and the viewport.
- Phone and desktop layouts differ (see Responsive), but every function works on both.
- The frontend-design skill (`.claude/skills/frontend-design/SKILL.md`) is not used to choose
  colours, fonts, components or layout: v8 has made those choices.

Source: the owner's design system v8 from Claude Design (2026-10-23), merged with the v1 rules
(owner brief, 2026-10-19) that still hold. Files:

| What | Where | Status |
| --- | --- | --- |
| Rules, lists, checklist | this file | current |
| Token values (JSON, platform-neutral) | `web/src/design/tokens.json` | `light` / `dark` / `shared` = what the app renders today (ADR 0018); `v8` = the new values, not wired yet |
| v8 tokens as CSS custom properties, three themes | `web/src/design/tokens.css` | landed, not imported |
| v8 component styles, prefix `qv-` | `web/src/design/bundle.css` | landed, not imported |
| Reference screens | `docs/design/screens/<screen>-light.png` | inspections, viewer, finding, report, sign-in, upload, states, themes, evidence-phone. Each file shows the light theme on top and the dark theme below it |
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
   a Letter sheet). No scores, confidence values or formulas anywhere in the product.
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

**Signal colours are reserved**: `critical` = finding `critical`, `watch` = finding `watch`,
`unseen` = `not_seen`, `ok` = `inspected_ok`. Never use them for decoration, for errors or for
anything else, so a red pixel always means a critical finding. Never a red button,
a coloured page background or a badge for decoration. The report is always a white document, in
every app theme.

## Themes

Three themes, Light, Dark and Midnight, from the same tokens (`[data-theme]` in `tokens.css`);
every signal colour has a value per theme and stays at 4.5:1 or better on `surface`. Light is as
complete as Dark. The theme follows `prefers-color-scheme` until a manual switch exists (a later
feature; Midnight is reachable only through it). Text the engineer must read is at least 4.5:1
in every theme. Borders and the focus halo use the v8 values as they are (owner decision,
2026-10-24). Keyboard focus is always visible (`shadow-focus`); respect
`prefers-reduced-motion`.

The 3D map is drawn on `map-ground` in every theme; only the chrome around it changes theme.

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
of the layout, not one radius everywhere. Phone portrait (390) has its own layout and the same
functions (see Responsive).

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
font sizes (12.5, 13.5, 15 px) and the sign-in gradient's colours. DS1 moves each of them into a
named token with the same value when it wires the file in: the values stay exactly as v8 has
them; only where they are written changes.

## Forbidden

- A colour, font, component or screen layout that is not in v8: agents never invent or change
  them. Allowed: alignment, spacing, sizes and responsive behaviour, so a screen fits real
  content and the viewport.
- A colour or length literal in a component; a token used for a meaning it does not have.
- `critical`, `watch`, `unseen` or `ok` for anything but critical, watch, not seen, inspected OK.
- Scores, confidence values or formulas on screen or in the report.
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
- Every function works at 1440x900 and at 390x844 (see Responsive).
- Before/after screenshots of every changed page at 1440x900 and 390x844, light and dark (see
  Process).

## Reviewer checklist (yes/no)

1. Do colours, fonts and components match the reference screen, with every value from a token
   (no literals)? Differences only in spacing, alignment or size are allowed.
2. Is there exactly one primary button on the screen?
3. Do `critical`, `watch`, `unseen` and `ok` appear only for critical, watch, not seen and
   inspected OK?
4. Does every number carry a unit, and is nothing readable below 13 px?
5. Is every text the engineer must read at 4.5:1 or better, in light and in dark?
6. Are all strings in i18n (en/ru/kk) and do the Russian and Kazakh versions fit without
   clipping?
7. Are labels sentence case, buttons verbs, dates in the app format (`21 Oct, 10:12`)?
8. Does every finding show frames with the mask before asking for a decision, and is the
   decision appended, never edited?
9. Does every function work at 1440x900 and at 390x844, with focus visible and motion off
   under `prefers-reduced-motion`?
10. Are there no gradients (except the sign-in hero), glow, blur, tinted backgrounds, emoji,
    confidence values or debug text, and is the map on `map-ground` in every theme?

## Process

- No new features or behaviour changes in a design pass: the same pages, the same data, the
  same tests (updated only where the markup changed).
- Before/after screenshots of each changed page come from the tester (`dev-*` = before), at
  1440x900 and 390x844, in light and dark.
- The reference screens in `screens/` are the target. The tester compares each screenshot with
  the matching reference: a different colour, font or component fails; a difference only in
  spacing, alignment or size is allowed. A PR that restyles a page links the matching screen in
  its description.

## Responsive

Two layouts, one set of functions. A function reachable on the desktop is reachable on the
phone, and the other way round; only the layout differs.

| Width | Layout | Reference screens |
| --- | --- | --- |
| 1280 px and wider | desktop: sidebar 248, top bar 48, side panel 460, map full-bleed | `inspections`, `viewer`, `finding`, `report`, `sign-in`, `upload`, `states`, `themes` |
| up to 767 px | phone, designed at 390 x 844: one column, a top bar with the mark, content as a conversation, an input bar at the bottom | `evidence-phone` |
| 768 to 1279 px | the desktop layout with the sidebar collapsed (its header toggle); panels may overlay the map | no own screen: the desktop references, narrower |

- The phone layout follows `evidence-phone-light.png`. Sign in, the inspection list, upload with
  its quick check and progress, the result with the map and the critical findings, and the input
  bar all appear in that order. A finding card on the phone opens the same finding as on the
  desktop: frames with the mask, size, status, confirm and reject.
- Nothing is hidden on the phone because it does not fit: it moves, stacks or opens on tap.
  Touch targets are at least 44 px.
- E2e runs at two viewports, **1440x900** and **390x844**, and both must pass. The tester's
  screenshots use the same two sizes.

## Decided: follow v8 exactly (owner, 2026-10-24)

Five points where v8 differed from the earlier rules. Each now stays as v8 has it; DS1 updates
the tests that pinned the old values. This is an owner-approved change, not test weakening.

1. **Contrast.** `line-strong` and the `shadow-focus` halo keep their v8 values, although they
   measure under 3:1 (`line-strong` 1.51 light, 2.14 dark, 1.89 midnight on `bg`; the halo 1.48
   to 2.16 on `surface`). Text keeps 4.5:1. The old v1 checks of 3:1 for `border-strong` and
   `focus-ring` move to the v8 values.
2. **Sign-in gradient.** The sign-in hero keeps its gradient, the one exception to "no
   gradients". `noLiterals.test.ts` allows it for the hero only, with its colours as tokens.
3. **Inspected-OK colour.** `inspected_ok` uses `ok`. The old `finding-inspected` blue
   (#5b9bff) goes.
4. **Confidence display.** No confidence values or scores on screen or in the report. A finding
   explains itself in plain words; low confidence is said in words (`watch`, "estimated").
5. **Canvas colour.** The 3D map is drawn on `map-ground` (#131313) in every theme. The old black
   (#000000) goes.

Two gaps in the v8 export, which DS1 closes without changing a value:

- `.t-body-italic` and `.t-quote` lack `font-style: italic`, although `tokens.json` sets it.
- Dark and Midnight apply only through `[data-theme]`; there is no
  `prefers-color-scheme: dark` block, so the app sets `data-theme` itself to follow the system.
