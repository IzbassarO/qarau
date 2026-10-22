# Qarau design rules

Short rules for every visual change in `web/` (and the Tauri shell around it). They come
first; the frontend-design skill (`.claude/skills/frontend-design/SKILL.md`, Anthropic,
Apache-2.0) is the general method. Where the two disagree, these rules win: the skill itself
says the brief's own words win.

## Product style (owner brief, 2026-10-19)

An enterprise industrial inspection tool, not an AI demo.

- Calm neutral palette with one accent; light and dark themes; clear hierarchy. The 3D viewer
  is the hero of the product.
- Never in the UI: gradients, glow, glassmorphism, emoji, AI-sparkle icons, marketing copy,
  developer or debug text. Plain language for engineers.

## Look: an engineering tool

- Calm, dense and readable: engineers check findings for hours. Information first; no hero
  blocks, no marketing copy, no decoration that carries no meaning.
- One visual language across the login page, the control centre, the run list and the viewer
  chrome. The point viewer itself stays as `docs/design/viewer-3d.md` says.
- Spend boldness in one place per screen (usually the data), keep the rest quiet.

## Tokens

All colours, type, spacing and radii are tokens, defined once in a platform-neutral JSON file
(`web/src/design/tokens.json`, light and dark) so the desktop and mobile apps reuse the same
values. `web/src/index.css` (`@theme`) takes its values from that file; components never use
literal values.

- **Colour.** Neutral greys for surfaces, lines and text; one interface accent for links,
  focus and the primary action.
- **Product colours are reserved.** Red = finding `critical`, orange = `watch`, gray =
  `not_seen`, base blue = `inspected_ok` (CLAUDE.md, trust rules). Never use them for
  decoration or for anything else, so a red pixel always means a critical finding.
- **Spacing.** 4 px base: 4, 8, 12, 16, 24, 32. Keep the spacing small and even.
- **Radius.** Small (4–6 px). Use the same radius for the same level of the layout, not
  one radius everywhere.
- **Type.** IBM Plex Sans for text and IBM Plex Mono for code and ids. Both are self-hosted
  (`@fontsource`): on-premise installs have no internet, so never load fonts from a CDN.
  Scale: 12, 13, 14 (body), 16, 20, 24. Use tabular figures for numbers in tables and stats.

## Light and dark

- Both themes come from the same tokens. Light is as complete as dark, and the theme follows
  `prefers-color-scheme` (a manual switch is a later feature, not part of a design pass).
- The 3D viewer canvas is always black in both themes (product rule). Only the chrome
  around it changes theme.
- Contrast is at least WCAG AA in both themes. Keyboard focus is always visible. Respect
  `prefers-reduced-motion`.

## Density and layout

- Body text is 14 px; table rows are about 32 px high. Lists and tables show many rows
  without scrolling the whole page.
- The run list shows the run name and its date clearly, newest first.
- Left-aligned text; numbers right-aligned in columns.
- Russian and Kazakh strings are often 30–40 % longer than English. Layouts must fit them,
  with no clipped text. All text goes through i18n (en/ru/kk), never hard-coded.

## Process

- No new features or behaviour changes in a design pass: the same pages, the same data, the
  same tests (updated only where the markup changed).
- Before/after screenshots of each changed page come from the tester (`dev-*` = before), at
  1440x900. Mobile-width screenshots come with a later Kerege change (multi-viewport
  screenshots).
