# DS1 part 1 — v8 tokens, theme and fonts

Scope: wire the existing v8 values in `web/`. Shell/component markup, phone layout,
the Playwright phone project and the v8 ADR remain for parts 2 and 3. No screenshots
or local e2e server: Kerege runs those checks from a clean checkout.

1. Flatten `tokens.json` to its v8 set and import `tokens.css` and `bundle.css`.
   Map existing CSS compatibility names to the v8 properties without retaining v1
   palette, typography, radius or spacing values.
2. Name the existing bundle colour/gradient literals and fractional font sizes in
   the token files, preserving every value; restore the two true italic styles.
   Extend literal tests so only the named sign-in hero gradient is permitted.
3. Initialize `data-theme` from the system before rendering and follow changes.
   Load the specified self-hosted font weights, with source-level regression tests.
4. Update every JSON reader and viewer canvas colour to v8, and replace old-look
   test expectations with fixed v8 fixtures. Keep text contrast checks at 4.5:1;
   pin the owner-approved v8 border and focus values.
5. Run `pnpm lint`, `pnpm typecheck`, `pnpm test` and `pnpm build` in `web/`.
   Inspect the final diff for scope, fixed expected values and no leftover server.

Licences: the owner-provided design is authorised for this task. The existing
Geologica, IBM Plex Sans, Lora and JetBrains Mono Fontsource packages are OFL-1.1,
which permits commercial use; verify installed package metadata before use.
No dependency or tool installation is needed.
