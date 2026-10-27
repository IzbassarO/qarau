# ADR 0026: Adopt design system v8

Date: 2026-10-26. Status: accepted (owner decision, 2026-10-24).

## Context

The owner's v8 design files define the product's colours, type, components and
desktop/phone layouts. ADR 0018 described the former v1 values. Maintaining both
palettes would let the shell and point viewer drift from the approved references.

## Decision

Adopt `docs/design/DESIGN.md`, its reference screens, `web/src/design/tokens.json`,
`tokens.css` and `bundle.css` as the single visual source of truth. **This ADR
supersedes ADR 0018's values**, while retaining its platform-neutral JSON token
approach. JSON contains one v8 set; CSS and the qv components consume that set.
Compatibility names resolve to v8, without a second palette.

The shell, sign-in, dialogs and viewer chrome reuse qv components. Adapt only
alignment, spacing, sizes and responsive behaviour. At 390×844 navigation opens
on demand and content stacks; at 768–1279 the sidebar is collapsed. Playwright
runs the same specs in 1440×900 and 390×844 projects with a fresh server.

Fonts are self-hosted: Geologica 600 for headings, IBM Plex Sans 400/500/600 and
italic for body, Lora italic for the tagline, JetBrains Mono 400/500 for identifiers.
The installed Fontsource 5.3.0 packages use OFL-1.1, permitting commercial use.
No dependency or CDN is added. React and Three.js remain MIT; Vite and Vitest MIT;
Playwright Apache-2.0; Tailwind MIT. The owner authorises the design files.

## Consequences

Light and Dark follow the system through `data-theme`; a Midnight switch remains
outside this task. Map ground is always `#131313`. Inspected OK uses v8 `ok`;
the accent is neutral. Borders and focus retain the owner's v8 values, including
their approved contrast exception; readable text retains its 4.5:1 checks.
The sign-in hero is the only allowed gradient. Its colours and the former bundle
literals are named tokens with unchanged values. True italic is applied to the
body-italic and quote helpers. Fixed v8 test fixtures replace the v1 expectations.

Screenshots and e2e execution belong to Kerege's tester; sandbox verification runs
lint, typecheck, unit tests, build and Playwright discovery without opening a port.
