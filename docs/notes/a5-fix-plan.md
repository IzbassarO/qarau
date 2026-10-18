# A5-fix — far-layer e2e without local data

The in-memory generator at `web/e2e/fixtures/far-layer/makeFarBundle.ts` creates a
version-1 manifest, 300 object points in a unit cube and 200 far points on a radius-3
shell. Buffers use little-endian XYZ float32, RGB uint8 and first-seen/observation
uint16. A 160 × 90 inline SVG supplies the frame image and thumbnail. No fixture is
read from or written to `data/`, and no binary assets are committed.

The spec serves the manifest and each fixture asset through exact `page.route`
handlers. Asset URLs use the production `runUrl` function, so
`web/points.position.f32` maps to
`/api/runs/synthetic-far/files/web/points.position.f32`, as requested by `loadBundle`.
Other API endpoints are not intercepted. The development viewer uses
`RequireAuth allowInDev`, matching the existing public demo test; it does not need a
test login or stored session.

The malformed case replaces only the far-position buffer with four bytes. It asserts
the visible broken-far warning, all 300 rendered object points and no far toggle.
The valid control checks 200 separate far points, no warning and successful toggling.
Both also check that no password form is present and that the SVG thumbnail decodes
to its expected natural width. Existing real-data `existsSync` skips are unchanged.

No dependencies or models were added. Installed licence files permit commercial use:
Playwright and TypeScript are Apache-2.0; Three.js and Vitest are MIT. The unrelated
`check` and `e2e:far-layer` package scripts from the previous round have been removed.

## Verification and remaining blocker

- Re-ran `pnpm lint && pnpm typecheck` and `pnpm test && pnpm build` (build output
  redirected with `--outDir` to task scratch): all passed, with 28 Vitest files / 499
  tests. Logs: scratch `lint-typecheck-revision2.log` and `test-build-revision2.log`.
  Two earlier scratch-only checks also loaded the generated valid/corrupt arrays
  through production `loadManifest`/`loadBundle`; these are not browser evidence.
- Playwright discovery lists both synthetic cases. Local browser runs with
  `QARAU_RUNS_DIR` pointing to the empty task scratch `empty-runs` directory stop before
  executing any tests: Vite cannot listen on `::1:5199`. Retrying with IPv4-first DNS
  also fails with `listen EPERM 127.0.0.1:5199`. The IPv4 attempt is recorded in scratch
  `playwright-revision2.log`. After the final test edits, the targeted and full-suite
  Playwright commands were retried and failed at the same server startup step; logs
  are scratch `playwright-final.log` and `playwright-full-final.log`. No passed e2e
  result is claimed.
- The orchestrator's actual checks are configured in protected `devteam/config.yaml`;
  GitHub CI's web steps are in protected `.github/workflows/ci.yml`. Neither currently
  runs Playwright. The developer cannot edit either file, commit/push this worktree,
  or dispatch an unpublished worktree to CI. No supported external-check command is
  exposed by the orchestrator CLI or available tools.
- Completion requires an orchestrator-owned check that runs
  `cd web && pnpm exec playwright test e2e/far-layer.spec.ts --reporter=list` outside
  the developer sandbox, with Chromium available and `QARAU_RUNS_DIR` set to an empty
  directory. Its output must show the malformed and valid synthetic tests passed;
  the existing Notre-Dame test may skip. Enabling that protected check is outside
  this developer's allowed changes. Screenshots are not needed.

All generated output belongs in the assigned A5-fix scratch directory. Playwright
must start its own fresh Vite (`reuseExistingServer: false`) and stop it afterwards.
