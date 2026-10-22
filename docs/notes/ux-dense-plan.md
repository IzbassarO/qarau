# UX-dense implementation plan

Read the ADR 0017 dense export without changing the pipeline. Load its XYZ, RGB and CSR
frame-link files through the existing authenticated bundle fetch into typed arrays. Keep
legacy depth-prior metadata and bundles without a dense layer unchanged.

Render the dense layer as one Points geometry, using the existing height ramp and colour
switch. New dense bundles open in height colour unless a user saved a colour preference;
existing bundles retain their defaults. Add the translated Dense toggle to the current
layer controls, enabled initially. Keep the black canvas, tokens, typography and existing
layout. Picking compares visible sparse and dense candidates and opens the same evidence
panel, with dense links mapped through the frames array and their source identified.

Check in a tiny reproducible fixture with four dense points and fixed CSR rows. Cover the
reader and evidence mapping in Vitest and toggle visibility and actual canvas picking in
Playwright. Run lint, typecheck, Vitest and build locally. Kerege runs Playwright and tester
screenshots, per this task's explicit instructions; no local server or screenshot is needed.

No new dependency or model. Installed package manifests confirm Three.js, React, Vite and
Vitest use MIT; Playwright uses Apache-2.0. These permit commercial use. Dense data is
read-only COLMAP output (BSD-3-Clause, ADR 0017).

## Verification

Implemented the reader, one dense Points geometry, translated toggle, shared colour controls,
cross-layer picking and evidence provenance. The four-point fixture covers exact CSR rows,
legacy/absent dense metadata, authentication and invalid buffers/links. Renderer tests cover
visibility, direct buffer reuse, picking, colour/size controls and disposal.

Lint, typecheck, all 574 Vitest tests and the production build pass. Playwright discovers all
72 browser tests, including the new fixture test. Browser execution and screenshots remain
with Kerege/the tester as explicitly required by this task. No server was started.

A local read-only smoke check bundled the production reader into the task scratch directory
and loaded the real notre-dame-p3e-modal files: 730,069 dense points, 4,547,022 frame links and
42,748 sparse points. This verifies reader compatibility with the real export; browser/GPU
appearance is left to the tester.
