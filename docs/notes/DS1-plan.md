# DS1 — design system v8 checklist

Current round: part 2 of 2, following acceptance of part 1. Continue the six
existing DS1 commits and preserve the accepted tokens, fonts and literal checks.
The initial audit found the qv shell/dialogs already wired, with remaining gaps
in viewer chrome, phone presentation, Playwright projects and the v8 ADR.

Part 2 plan: reuse the existing qv components for the inspection catalog and
viewer controls, fit the phone layout to the evidence-phone reference without
adding features, adapt existing e2e interactions to collapsed navigation, pin
both viewport fixtures, add ADR 0026, and run the required local checks.
Kerege executes e2e and screenshots; no local server or screenshots are started.

## Build items 1–6

- [x] **1. Wire v8 tokens and bundle.** `src/index.css` imports `tokens.css` and
  `bundle.css` once and maps the compatibility colour, font and radius names to v8.
  `main.tsx` calls `followSystemTheme()` before rendering; it sets `data-theme` on
  the HTML element immediately and follows system changes. `tokens.json` contains
  one v8 set (`version: 8`), without the former v1 groups or a duplicate `v8` group.
  Both italic helpers have `font-style: italic` in the bundle. Covered by
  `tokens.test.ts` and `theme.test.ts`.
- [x] **2. Self-host the specified fonts.** `main.tsx` imports Geologica 600,
  IBM Plex Sans 400/500/600 and 400 italic, Lora 400 italic, and JetBrains Mono
  400/500. No IBM Plex Mono import or font CDN remains in application sources or
  `index.html`. The unused installed IBM Plex Mono dependency is not imported.
  `fonts.test.ts` covers these requirements; the production build emits local font
  assets, including Cyrillic and Cyrillic extended where supplied by the packages.
- [x] **3. Apply qv components and responsive chrome — part 2.** Retain the
  inherited qv shell, sign-in, processing states and upload dialog. Present the
  existing API inspection catalog as qv-list rows in the main area and cards on
  phones, without inventing findings or review counts absent from the API. The
  sidebar has its header collapse action and the phone topbar has the mark.
  Phone sign-in uses the evidence-phone header and centered form. Viewer chrome
  now uses qv-screen, qv-map, qv-modes, qv-pill, qv-float, qv-pop, qv-toggle,
  qv-thumb, qv-heading and qv button components. Frames/layers, camera tools and
  settings open on demand over the map, one panel at a time. Panels scroll inside
  the map workspace. Phone axis controls use a grid with 44 px touch targets;
  timeline controls wrap and the minimap scales. All new labels have en/ru/kk
  translations. Tester visual comparison remains pending; no screenshot claim.
- [x] **4. Name the former bundle literals.** White, dark ink, the 12.5/13.5/15 px
  font sizes and sign-in hero gradient reference named CSS tokens with matching
  JSON values. Their original values are preserved. `tokens.test.ts` pins those
  values; `noLiterals.test.ts` rejects UI colour literals outside the token files
  and permits the named sign-in hero gradient only on `.qv-hero::before`.
- [x] **5. Move old-look tests to fixed v8 fixtures.** `tokens.test.ts` pins the
  full v8 palettes, including light OK `#1a7040`, dark OK `#6bd39b`, light accent
  `#111111`, dark accent `#f2f2f0`, map ground `#131313`, JetBrains Mono and the
  owner-approved border/focus values. Readable text retains its 4.5:1 checks.
  `viewer/engine.test.ts` expects clear colour `#131313`; `e2e/far-layer.spec.ts`
  expects the fixed light unseen colour `6f6f6b` and canvas `rgb(19, 19, 19)`.
- [x] **6. Playwright phone project and v8 ADR — part 2.** The desktop project
  fixes 1440×900 and the phone project fixes 390×844. `playwright-config.test.ts`
  asserts both literal configurations and retains `reuseExistingServer: false`.
  ADR 0026 adopts v8 tokens, fonts and bundle.css and explicitly supersedes
  ADR 0018's values.

## Verification for part 2

- Baseline `pnpm lint && pnpm typecheck && pnpm test && pnpm build` passed before
  implementation: 37 test files and 715 tests. The build had only its existing
  large-chunk advisory. No part 1 token/font/literal expectations were rewritten.
- Final local checks: `pnpm lint`, `pnpm typecheck`, `pnpm test` (37 test files,
  718 tests, including noLiterals, keys and fixed viewport fixtures) and
  `pnpm build`. Production output contains the existing large-chunk advisory.
- Component coverage exercises catalog selection, sidebar collapse/restore and
  qv viewer mode/point-panel classes. Existing sign-in and upload component
  assertions are retained. E2e follows collapsed navigation and foldable viewer
  panels; desktop density assertions remain, while phone rows pin 44 px targets
  and exercise scrolling through every entry. New viewer chrome checks cover both
  themes and all three languages, font families, fixed map ground, control hit
  targets, panel exclusivity, layer toggles and horizontal overflow.
- `pnpm exec playwright test --list` discovers 208 tests in 12 files across the
  desktop and phone projects. This is discovery, **not an e2e execution pass**.
  The task explicitly assigns e2e execution and screenshots to Kerege outside
  this sandbox. No new tests are skipped, and no server or screenshot capture
  was started here. Final visual acceptance for `/`, `/?signed-out` and
  `/viewer/notre-dame-p3e` remains with the tester.
- Installed package metadata was checked: Fontsource Geologica, IBM Plex Sans,
  Lora and JetBrains Mono 5.3.0 are OFL-1.1; React, Three.js, Vite, Vitest and
  Tailwind are MIT; Playwright is Apache-2.0. These permit commercial use. No
  dependency, model, tool or skill was added. The owner's design files are
  authorised for this task.

## E2e return: folded viewer panels

Kerege returned the first part 2 round before visual review: phone viewer chrome
and A6 controls timed out, and the missing/broken findings warning was hidden.
The open data panel could cover the phone Settings summary. The error message
was also inside that folded panel, so it was invisible in the initial view.

- Group the existing summaries and mode controls in one responsive overlay grid.
  Anchor all three dropdowns below its actual height, including wrapped ru/kk
  labels and Observer controls. Bound their scrolling area using the surrounding
  workspace and reserve the timeline. Keep v8 components, palette and fonts.
- Move findings failures to the existing notice area above the map. Add two
  regression fixtures for missing and broken files, checking the exact messages,
  a single alert outside any details element, a closed data panel and a usable map.
- Keep every previous e2e assertion, threshold and expected value. Update the two
  summary selectors to the shared wrapper; assert that both summaries are present
  and add checks that open tool/data panels start below all header controls.
- Local lint, typecheck, 720 unit tests (37 files) and build pass. Playwright
  discovery still covers both viewport projects. Full e2e execution remains with
  Kerege. A browser-only diagnostic without a server was rejected by the sandbox
  at Chromium's Mach-port bootstrap; it exited and cleaned its temporary profile.
  No listening server or screenshot capture was started. No dependency was added;
  the commercial-use licences recorded above remain unchanged.
