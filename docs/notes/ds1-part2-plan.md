# DS1 part 2: shell, sign-in and dialogs

Use the accepted v8 tokens, fonts and component bundle without changing their values.
Reference layouts: sign-in, inspections, upload, states and themes in `docs/design/screens/`.

1. Replace shell/auth presentation with the existing qv shell, navigation, typography,
   form, dialog, card, notice and empty-state classes. Preserve authentication, API data,
   upload lifecycle, navigation and all existing translation keys.
2. Reduce `shell.css` to token-based layout, spacing, size and overflow adjustments.
   Preserve access to controls on narrow screens; the full phone/viewer pass stays in part 3.
3. Pin the qv classes with fixed string assertions in the five component test suites,
   retaining their behaviour assertions and en/ru/kk coverage.
4. Run lint, typecheck, all unit tests and build. Kerege runs e2e and screenshots;
   do not start a server or capture screenshots in this worktree.

No new dependency or model. Existing React (MIT), React Router (MIT), TanStack Query (MIT)
and Fontsource fonts (OFL-1.1) permit commercial use, as documented in their installed
packages and the task brief. The v8 design assets belong to the owner.

Part 1 remains intact. Viewer chrome, the phone Playwright project and the ADR remain part 3.

## Verification

`pnpm lint`, `pnpm typecheck`, `pnpm test` (37 files, 714 tests) and `pnpm build` passed.
The existing noLiterals checks passed. `git diff --check` passed. No server was started.
Kerege still runs browser e2e and the visual comparisons in light/dark and en/ru/kk.

The bundle reset uses low-specificity element selectors so it does not override the v8
component weights and spacing. Native dialog backdrops and upload progress reuse the
existing scrim and progress tokens; the file picker uses the existing focus shadow.
The navigation scrim remains flat. None of the token values changed.
