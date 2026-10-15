# A2a browser evidence

The returned review requested a screenshot attributable to the replacement controls.
No screenshot was supplied in the worktree, and no existing image can be attributed to
this source by its filename alone.

The browser scenario is `web/e2e/navigation.spec.ts`. It verifies the served navigation,
engine, demo route and fixture source against the current worktree, checks that the
rendered engine uses `NavigationControls`, exercises the navigation gestures and bounds,
and asserts a black clear colour with no browser errors or Vite/WebGL error overlay.
Only after those assertions complete does it write SHA-256 hashes for the source and
both screenshots to `web/test-results/a2a-controls/provenance.json`. The screenshots and
manifest are also attached to the Playwright test result. No commit is needed: the
manifest identifies the actual uncommitted source content.

Its JSON translation import now declares the import type required by Node's ESM
loader; without it, Playwright could not discover this scenario. Test discovery with
`pnpm exec playwright test --list e2e/navigation.spec.ts` now succeeds (one test).

Rerun from a browser-enabled checkout with the existing dependencies:

```sh
cd web
pnpm exec playwright test e2e/navigation.spec.ts
```

Inspect both screenshots, then retain `demo-navigation.png`,
`demo-after-navigation.png` and `provenance.json` from
`web/test-results/a2a-controls/` in this directory. The manifest is removed when the
test starts so a failed rerun cannot reuse a previous success record.

Require a successful Playwright exit as well as matching source/screenshot hashes.
A failure before the test body starts (for example, Vite failing to bind) cannot
invalidate an older manifest automatically and never proves a successful new run.

## Current sandbox rerun

On 2026-10-15 the exact command above failed before a test could start because Vite
could not open its listening socket:

```text
Error: listen EPERM: operation not permitted ::1:5199
Error: Process from config.webServer was not able to start. Exit code: 1
```

This attempt produced no screenshot and no successful provenance record. The browser
acceptance criterion remains unverified in this sandbox. An externally supplied tester
screenshot still requires confirmation that it used the replacement controls.
