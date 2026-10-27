# DS1 — design system v8 checklist

## Tester return: visible phone gradient and map overlays

The latest feedback permits a clearly visible full-width phone gradient as an
alternative to the plain phone bar, while requiring unchanged e2e assertions.
Use that alternative: retain the existing 56 px hero and fixed gradient fixture,
but scale its painted layer down to fit the phone. The export's absolute radial
sizes currently paint only a near-uniform crop in the narrow band. Change only
the pseudo-element's dimensions/transform, preserving its colours and image.

Anchor the viewer hint at the bottom of the map above the timeline, using the
existing qv-map__hint component. Remove the conflicting tablet/phone vertical
offsets; reserve a separate bottom lane for wrapped hints by lifting the phone
gizmo/minimap. Keep the hint available for the existing navigation assertions.
Give the gizmo and minimap a qv-float map variant using existing map-ground,
map-grid and map-text, including axis buttons and the minimap label. Preserve
the theme-following styling of other panels.

Leave all existing e2e files unchanged. Add separate fixed-value regressions for
the dark map floats and bottom hint in both themes, three languages and both
projects. Run local lint, typecheck, tests, build and Playwright discovery;
Kerege retains browser execution/screenshots. Existing Vite/Vitest (MIT) and
Playwright (Apache-2.0) licences permit commercial use; metadata rechecked.
No new dependency, token value, server or screenshot capture is needed.

Completed the scale adaptation and the map-float variant. Consolidated the
hint rules at bottom: 12 px; phone navigation aids now leave a separate lane
for translated hint text. Added `map-overlays.spec.ts` with fixed map colours,
the fixed bottom offset and no-overlap assertions for Orbit and Observer.
All pre-existing e2e files remain byte-for-byte unchanged.

Verification: pnpm lint, pnpm typecheck, all 725 unit tests in 37 files (including
noLiterals), pnpm build and git diff --check pass. Build retains its existing
large-chunk advisory. Playwright discovery reports 236 tests, 118 per project,
without errors. Browser e2e and visual acceptance remain assigned to Kerege.
No server, browser or screenshot capture was started.

## Tester return: preserve the phone sign-in gradient

Criterion 4 requires the v8 hero gradient at both viewport sizes. The inherited
phone CSS replaced its background and mark colours with surface colours, and
shell.css hid its ::before entirely. Remove both overrides and the pseudo-element
display:none selector. Keep the compact 56 px band, centered form and existing
v8 background, gradient, light ink and white mark tile in both themes.

Add e2e assertions for the fixed exported hero/mark colours and the complete
gradient fixture, with the pseudo-element generated, displayed and visible.
Apply them to the existing desktop/phone and explicit phone sign-in cases in
en/ru/kk and Light/Dark. Retain every previous assertion and expected value;
the transparent phone form remains valid and no fixed expectation conflicts.
No token, font or dependency changes. Existing Playwright (Apache-2.0), Vite and
Vitest (MIT) permit commercial use; installed metadata rechecked.

Run lint, typecheck, unit tests and build, plus Playwright discovery. Kerege
executes e2e and captures screenshots under the task's sandbox rule; start no
server or browser locally.

Verification completed: pnpm lint, pnpm typecheck, all 725 unit tests (37 files,
including noLiterals) and pnpm build pass. The existing large-chunk advisory
remains. Playwright discovery succeeds for 224 tests, 112 in each project.
Every pre-existing sign-in assertion and fixed expectation is retained; the
gradient checks are additions only. Browser e2e and screenshot acceptance remain
with Kerege. No server, browser or screenshot capture was started.

## Current round: part 2 audit (2026-10-26)

Continue the existing `devteam/DS1` implementation at `4fa5049`; do not redo
accepted part 1. Read CLAUDE.md, DESIGN.md and the sign-in, inspections, upload,
states, themes, viewer and evidence-phone reference images (Light above Dark).
The worktree was clean before this audit.

| Build item | Current branch evidence | Gap / action |
| --- | --- | --- |
| 1. Wire v8 | index.css imports tokens.css and bundle.css once; aliases resolve to v8; system data-theme follows media changes; JSON has one v8 set; italic helpers are present. | Done in accepted part 1; preserve. |
| 2. Fonts | main.tsx self-hosts the specified weights/styles of all four families, with no IBM Plex Mono import or CDN. | Done in accepted part 1; preserve. |
| 3. qv screens and phone | Shell, sidebar, sign-in, upload dialog, processing rows and viewer already use the v8 qv classes. The phone sidebar folds, cards stack, viewer panels share an adaptive control grid and touch targets expand. | Remove remaining redundant qv button/icon presentation overrides in viewer.css; use qv-muted for the run identifier. Preserve the inherited layouts and domain-specific map styling. Visual acceptance remains with the tester. |
| 4. Literals | Former bundle literals live in named tokens; noLiterals allows only the tokenised hero gradient. shell.css and viewer.css have no colour or font-family literals. | Done in accepted part 1; rerun guard with the full unit suite. |
| 5. Old-look tests | Existing tests pin the v8 palettes, OK, map ground, neutral accent, border/focus and JetBrains Mono as fixed fixtures. | Done in accepted part 1; preserve every assertion. |
| 6. Playwright and ADR | Both fixed viewport projects and their literal unit expectations exist, with reuseExistingServer: false. ADR 0026 already adopts v8 and supersedes ADR 0018's values. | Keep the config and single existing ADR. Add the explicitly requested missing web/e2e/phone-layout.spec.ts with fixed scrollWidth <= 390 assertions for / and /viewer/notre-dame-p3e. |

Implementation plan: close only these gaps, add phone regressions using existing
API fixtures and translated labels, then run pnpm lint, typecheck, test and build.
Discover the complete Playwright suite without starting a server. Per the task's
explicit sandbox rule, Kerege executes e2e in both projects and captures all
screenshots; do not launch a browser/server or take screenshots locally.

Installed package metadata checked for this round: the four Fontsource packages
are OFL-1.1; React, Three.js, Vite and Vitest are MIT; Playwright is Apache-2.0.
All permit commercial use. No dependency, model, tool or skill is added.

Part 2 gap work completed:

- Added `web/e2e/phone-layout.spec.ts`, with a literal 390×844 viewport and
  document `scrollWidth <= 390` assertions on `/` and `/viewer/notre-dame-p3e`.
  Root checks cover en/ru/kk in both themes, collapsed/open navigation and the
  upload dialog. Viewer checks cover both themes, the fixed map ground, no
  error banner and open details/settings panels. No screenshot capture is added.
- Removed local playback colours, button disabled-state overrides, icon and
  thumbnail hover overrides. The existing qv components now supply those styles;
  the run identifier uses qv-muted instead of duplicating it in viewer.css.
  No token, font, bundle value, existing test assertion or screen layout changed.
- The inherited two-project config, literal config tests and ADR 0026 already
  satisfy item 6; no second ADR or duplicate implementation was needed.

Verification: pnpm lint and pnpm typecheck pass; pnpm test passes all 725 tests
in 37 files, including noLiterals; pnpm build passes with the existing large
JavaScript chunk advisory. Playwright discovery finds 224 tests in 13 files,
including this regression in both projects. Discovery is not an e2e pass.
Kerege still needs to run the full browser suite and judge screenshots in both
themes, sizes and all three languages. No server or browser was started locally.

## Previous round: part 1 audit (2026-10-26)

Continue `devteam/DS1`, including the ten existing task commits through
`1c9138a` and the merge from dev. The worktree was clean before this audit.
This round covers build items 1, 2, 4 and 5 only; preserve inherited part 2 work.
Read `CLAUDE.md`, DESIGN.md and all nine reference screens, including the
Light/Dark pairs and the 390×844 evidence-phone reference.

| Build item | Audited branch state | Missing work in this round |
| --- | --- | --- |
| 1. Tokens and bundle | Done: one version-8 JSON set; Light/Dark aliases in index.css; tokens.css and bundle.css imported once; system data-theme applied before render and subscribed to changes; both italic helpers present. | No implementation gap found. Run the existing token and theme checks. |
| 2. Fonts | Done: main.tsx imports all eight required local weight/style files; application sources and index.html contain no IBM Plex Mono import or font CDN. The unused package declaration is not an import. | No implementation gap found. Verify font tests and production font output. |
| 3. qv screens and phone | Inherited implementation covers shell, sign-in, sidebar, dialogs and viewer chrome, with phone adaptations. | Part 2 and tester visual acceptance; do not change screens in part 1. |
| 4. Literals | Done: former white/black, 12.5/13.5/15 px and sign-in gradient literals are named CSS/JSON tokens; bundle values are preserved. noLiterals restricts the sign-in gradient to the hero. | No implementation gap found. Run the existing literal guard. |
| 5. Old-look tests | Done: fixed v8 fixtures pin OK, neutral accent, border/focus values, JetBrains Mono, canvas #131313 and far-layer unseen colour. Expected values are independent literals. | No implementation gap found. Run the full unit suite; Kerege runs e2e. |
| 6. Playwright and ADR | Inherited desktop 1440×900 and phone 390×844 projects retain reuseExistingServer: false; ADR 0026 supersedes ADR 0018's values. | Part 2 acceptance; preserve the existing config and ADR. |

Plan after audit: run lint, typecheck, tests and build on the inherited code;
fix only a demonstrated part 1 failure. Do not restyle screens or rewrite working
code to duplicate earlier commits. Kerege runs e2e and captures screenshots
outside this sandbox, as required by the current task. Start no local server.

Installed package metadata rechecked: the four Fontsource packages at 5.3.0
are OFL-1.1; React, Three.js, Vite and Vitest are MIT; Playwright is Apache-2.0.
All permit commercial use. No dependency, tool, model or skill is added.

Initial-round verification completed on the inherited code:

- `pnpm lint` and `pnpm typecheck` pass.
- `pnpm test` passes all 720 tests in 37 files, including the fixed v8 tokens,
  fonts, system theme, noLiterals and viewer canvas assertions.
- `pnpm build` passes and emits local assets for all four font families, with
  the existing advisory about the JavaScript chunk exceeding 500 kB.
- `pnpm exec playwright test --list` discovers 208 tests in 12 files across
  both viewport projects. This is discovery only; e2e execution and visual
  comparison in en/ru/kk and Light/Dark remain with Kerege.
- No implementation gap or failing part 1 check was found, so the initial round changed
  only this audit/plan. No test, token, bundle value, screen, config or ADR was
  changed. No server or screenshot capture was started.

## E2e return: phone Orono dense screenshot timeout

Kerege reported 207 passes and one timeout while capturing the phone Orono dense
viewer after reset/point clicks. The fonts had loaded. Preserve every e2e
assertion and its timeout. This is a viewer rendering fix required by the gate.

A CPU-side diagnostic in the task scratch directory loaded the actual Orono
dense bundle (2,101,189 points), using the production loader and real camera math
with a mocked renderer. Initial, scrubbed, source-camera and reset views all
settled with zero idle draws across the following 60 ticks. Thus the diagnostic
does not support an idle-camera redraw loop. It cannot reproduce compositor/GPU
timing, and no browser screenshot or server was started.

The draw loop currently submits another full-cloud draw without checking GPU
completion. Under slow software rendering and concurrent browser tests, that
can build a queue that screenshot capture must drain. Plan: use a nonblocking
WebGL2 fence to keep one draw in flight, retain dirty changes until the GPU is
ready, and coalesce changes to their latest state. Defer the initial draw to
animation-frame time so React StrictMode's discarded setup does no GPU draw.
Keep every point, font, colour, size, shader and interaction. Add regression
coverage for a busy GPU, latest-state redraw, disposal and initial scheduling,
then rerun the required local checks. Kerege must verify browser timing.

Implemented the nonblocking fence and deferred first draw, with five scheduling
regressions. A scratch run of these regressions against the previous engine
fails all five: the simulated busy-GPU case submits 50 draws rather than one.
The current engine holds at one until completion, renders the latest fixed frame
fixture (11), and then idles. Both WebGL completion statuses and fence disposal
are covered. This reproduces unbounded submission, not the browser screenshot
timeout itself; Kerege must confirm the latter.

The component renderer mock now implements the WebGL2 sync API. Three existing
component tests explicitly advance their mocked animation frame before checking
the initial draw; asynchronous loading advances it inside the existing waitFor.
Every existing assertion, expected value and threshold is retained. The e2e
files, Playwright config, v8 tokens, fonts, styles and shaders are unchanged.

Final local lint, typecheck, all 725 tests in 37 files and production build pass.
The build retains its existing large-chunk advisory. Three 0.186.1 (MIT) already
requires WebGL2; its installed source confirms the sync API is available despite
the legacy context union in the type definitions. No new dependency is used.
No server or screenshot capture was started. Full e2e remains assigned to Kerege.

## Earlier rounds (inherited implementation and verification)

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

## E2e return: Point info dismissal and sign-in synchronisation

Kerege's next run finished with 205 passes and three failures: Point info's
post-dismissal button was inaccessible on desktop and phone, and the desktop
sign-in/session test failed. The earlier panel-layout and findings-alert checks
passed in that run.

- Closing point evidence now reopens its originating tools drawer and focuses
  the Point info button. Picking state and mode still reset; the control remains
  reachable with `aria-pressed="false"`. The existing unit test retains every
  assertion and additionally checks the reopened drawer and restored focus.
- The shared e2e navigation helper waits for the signed-in shell to attach before
  deciding whether navigation is collapsed. This removes its race with the
  asynchronous sign-in request: a desktop run must not attempt to click the
  intentionally hidden sidebar trigger while the login form is still mounted.
- No e2e spec assertion, expected value, threshold or timeout changed. No new
  dependency or visual value was introduced. Local lint, typecheck, all 720 unit
  tests (37 files) and build pass. Kerege must rerun the full browser suite;
  the sandbox restriction remains, and no local server or screenshots were started.
