# UX1a — design system, login and control centre

Use cool neutral surfaces (#f3f4f5, #ffffff, #e9ebee in light; #181a1d,
#202327, #2a2e33 in dark), strong neutral text and one muted violet accent
(#6550a2 / #b6a2e0). Berry form errors are separate from reserved red critical,
orange watch, gray not-seen and blue inspected colours. The viewer remains black.

Tokens live in `web/src/design/tokens.json`: themed colours; shared finding/canvas
colours; system font stack, six type sizes, weights and line heights; a 4 px spacing
scale; 4/5/6 px radii; reusable layout dimensions. Numeric dimensions are platform
neutral, interpreted as pixels by CSS. No fonts, libraries or models are added.
Existing React/Vite/Vitest/TypeScript dependencies are MIT; Playwright is Apache-2.0,
all commercially usable, as recorded in the task brief.

Login: a centred, bordered form card with a text wordmark, left-aligned labels and
one full-width primary action. Control centre: a quiet sites/inspections rail and
an aligned main heading; concise empty state; keep the existing inspection block
order and content, with the map as the largest panel. Avoid decorative cards, uppercase
eyebrows and marketing copy. Keep routes, controls, handlers and requests intact.

Review against the product brief: violet distinguishes navigation/actions from
finding colours; neutral processing states use icons and explicit text rather than
finding colours. Flexible rows and wrapping accommodate Russian and Kazakh.

Touch `web/src/index.css`, new design token/tests, auth login markup/styles, shell
components/styles, and the three i18n dictionaries. Keep viewer source unchanged;
retain its existing CSS variable names as aliases to the new tokens, and preserve
all four finding/coverage colours exactly. Update only
markup/style-dependent existing assertions and add theme/token coverage.

Verify with pnpm lint, typecheck, test and build. The tester runs Playwright and
captures before (`dev-*`) and after login and signed-in home at 1440×900. Do not
take screenshots separately. Do not edit progress.md.

Round 2: run the requested themed, far-layer and orono-dense Playwright checks.
The sandbox rejects Vite's listening socket (EPERM) and Chromium's Mach bootstrap
registration, including a browser launch without a server. Keep those limitations
explicit; the tester must execute the browser checks. Scope the home heading
selector to main with an exact accessible name, since the sites heading also
contains “inspections” in all three languages. Put sidebar dates below titles;
preserve strict overflow assertions and report the offending element on failure.
