# DS1 — design system v8 checklist

Current round: part 1 of 2. Continue the six existing DS1 commits; do not rebuild
completed work. The baseline audit on 2026-10-26 found no missing part 1 code.
Part 2 starts only after the tester accepts part 1.

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
- [ ] **3. Apply qv components and responsive chrome — part 2.** Prior commits
  already apply qv classes to the shell, sign-in, sidebar and dialogs and include
  responsive CSS. Their completeness and the viewer chrome still need the part 2
  audit against the reference screens, in both themes and all three languages at
  1440×900 and 390×844. This round makes no layout changes or screenshot claims.
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
- [ ] **6. Playwright phone project and v8 ADR — part 2.** The current config
  still uses only the 1440×900 default viewport, with `reuseExistingServer: false`.
  Add the 390×844 project and an ADR superseding ADR 0018's values in part 2.

## Verification for this round

- `cd web && pnpm lint && pnpm typecheck && pnpm test && pnpm build` passed on
  the inherited code: 37 test files, 715 tests, including the token, literal, font,
  theme and viewer engine tests. Build succeeded with the existing large-chunk
  advisory.
- `cd web && pnpm exec playwright test --list` passed: 98 tests in 11 files.
  This lists tests only; it does not establish an e2e pass. Per task instructions,
  Kerege runs e2e and captures screenshots with its fresh server outside this
  sandbox. No server was started in this round.
- Installed Fontsource package metadata confirms Geologica, IBM Plex Sans, Lora
  and JetBrains Mono 5.3.0 use OFL-1.1, permitting commercial use. No dependency,
  model, tool or skill was added. The owner's design files are authorised for use
  in this task.

This round adds the missing task checklist and corrects the older part 1 note's
phase count. Existing part 1 implementation and test expectations are unchanged.

