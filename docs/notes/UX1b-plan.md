# UX1b — run list and viewer chrome

Use the UX1a neutral surfaces, borders and violet interface accent in both themes.
The existing light palette is background #f3f4f5, surface #ffffff, raised #e9ebee,
text #24282e, muted text #59616b and accent #6550a2; dark values remain paired in
tokens.json. The black point canvas and semantic finding colours stay intact.
IBM Plex Sans carries interface text and IBM Plex Mono carries identifiers; use
the already installed, self-hosted fonts (OFL-1.1, commercial use permitted).
The pinned frontend-design skill is Apache-2.0. No dependencies or assets added.

1. Give inspection rows a 32 px token, a single ellipsized name with a full-name
   tooltip, and an intrinsic-width date column. Keep site groups and existing
   newest-first adapter ordering. Compact header/footer spacing leaves room for
   at least twelve rows at 1440×900, including Russian and Kazakh labels.
2. Keep the descriptive empty-state instruction and remove emptyNote in all three
   locales. Remove the footer's redundant example badge, retain badges on the
   demo inspection and retain its notice.
3. Align viewer headers, panels, controls, timeline and overlays to the same
   spacing/type/radius tokens. Use opaque themed chrome, sentence-case section
   headings, and the interface accent for controls. Keep semantic colours on
   findings and coverage. Leave engine, navigation and input logic alone.
4. Add focused sidebar/empty-state assertions and browser layout checks for twelve
   localized rows. Preserve all existing behavioural checks. Run lint (including
   Prettier), typecheck, full Vitest and build. Kerege owns e2e execution and tester
   screenshots; do not start a server or take screenshots in this sandbox.

Layout review: inspection names and dates form a compact ledger rather than
multi-line cards. Viewer evidence sections use quiet dividers and the same type
hierarchy as site groups, leaving the point cloud as the visual focus.

Verification: lint (ESLint and Prettier), typecheck, all 538 Vitest tests across 31
files, and the production build passed. Playwright discovered 70 tests with
--list; execution and before/after screenshots remain with Kerege. No server was
started. The protected design/i18n gate tests, navigation and engine files, and
dependency manifests were left unchanged.

E2e follow-up: Kerege passed 68 tests and found two kk date assertions comparing
Node's ICU output with Chromium's. Compute the exact expected Intl date in the
browser, using the explicit requested locale and fixture timestamp. Keep the
text equality, twelve-row threshold, ordering and clipping assertions intact;
application formatting is unchanged. Kerege will rerun the browser suite.
