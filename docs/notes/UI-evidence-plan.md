# UI-evidence — implementation plan

Only part 1 is implemented in this round. Backend files and design sources are read-only.

1. Mirror DEF1 and REV1 JSON contracts, enums and session roles in a typed client using the existing API transport and TanStack Query keys. DEF1 lists by inspection, so resolve a run through inspection metadata and filter by the exact API run ID. Keep unsupported legacy catalogs usable without presenting fabricated findings as real observations.
2. Add pure contain-fit and pixel polygon transforms with fixed numeric expectations. Format area from the API measurement and scale source, with translated units and an explicit missing-scale label.
3. Build the read-only evidence card using existing v8 detail, thumbnail and mask components. Show loading, readable failure, empty evidence, class, severity, effective status, area and scale source. Load protected frames through SessionImage; use natural image dimensions for SVG alignment. Never display confidence.
4. Connect API findings to map point indices and markers, preserving bundle finding IDs. Support `?finding=<id>` and `?finding=first`; opening evidence replaces point info. The reference notre-dame-p3e deep link includes the checked-in fixture as a fallback when its API has no findings or is unsupported, including compiled previews. Real API findings retain priority; other viewer URLs use the backend.
5. Test fetch URLs, authenticated decision POST bodies, fixture parsing, panel states, outline coordinates, area text and viewer opening/picking with mocked APIs. Run lint, typecheck, all Vitest tests and production build. No screenshot capture or server is needed; Kerege runs browser checks.

Parts 2 and 3 remain separate: append-only decision log and role-based actions; broader locale/phone validation and Playwright fixture e2e. New part-1 copy still uses the existing ru/kk/en dictionaries to avoid hard-coded interface text.

References: `backend/app/findings.py`, `decisions.py`, `auth.py`, model enums and OpenAPI tests; `docs/design/DESIGN.md`, `viewer-3d.md`, `screens/finding-light.png` and `screens/evidence-phone-light.png`.

Licences checked in installed package metadata: React, Three.js, TanStack Query, Vitest and Testing Library are MIT, permitting commercial use. Existing Playwright is Apache-2.0 per the task contract. No dependencies or models are added.

OpenAPI verification: the full app import needs unavailable `rq`. Using the existing backend and pipeline virtual environments, generated OpenAPI directly from the unmodified findings, decisions and CRUD routers in a temporary in-memory FastAPI app. Verified FindingOut, EvidenceOut, DecisionIn/Out, RunOut and every enum; no dependency installation, backend edits or server.

The reference preview is opt-in through `/viewer/notre-dame-p3e?finding=first` or `?finding=701`. Its bundled fallback is independent of Referer, server paths and API origin. The older Vite shim remains compatible; its Referer and filesystem checks no longer determine whether the control URL can show evidence. Other runs and ordinary viewer URLs keep their existing sources.

## Part 1 verification

- `cd web && pnpm lint && pnpm typecheck` — passed.
- `cd web && pnpm test && pnpm build` — passed, 43 test files and 801 tests. The build retains Vite's large-chunk advisory; no build errors.
- Literal contain-fit coordinates, scaled/unscaled area, fetch URLs/session headers, decision POST payloads, loading/error/retry/empty/loaded panel states, hidden confidence, natural image dimensions and resize, large finding IDs and point picking, marker selection and both deep links are covered.
- All three fixture image files exist in the read-only notre-dame-p3e bundle. Preview middleware is covered without starting a server.
- No servers, screenshots or Playwright runs were started. Kerege performs browser verification; full fixture e2e and phone work remain in part 3. Decision log/actions remain in part 2.

## E2e repair round

Kerege reported unrelated inspection-run discovery requests and phone controls blocked by evidence opened for legacy bundle findings. Resolve explicit inspection `run_id` metadata before scanning RunOut records, keep legacy-only findings on the existing timecode panel, and stack the mobile evidence panel below the controls. Add regression coverage and the new findings response to the existing inspection e2e mock; preserve all its assertions. Run the required local checks; browser execution remains with Kerege.

Repair verification: lint and typecheck passed; all 804 Vitest tests in 43 files passed; production build passed. Existing test assertions are unchanged. Added phone Playwright coverage for opening viewer menus while evidence is visible in both themes; it awaits Kerege's browser run. Confirmed the two failing run bundles contain legacy findings IDs. No servers or screenshots were started.

## Screenshot fixture repair

The control URL must work when its API has no findings, including a compiled preview with no Referer header. Include the existing reference finding in the client bundle, scoped to the explicit notre-dame-p3e finding deep links and used only for an empty or unavailable findings API. Keep real API findings authoritative and authentication/server failures visible. Pass reference data directly to the read-only panel instead of requesting its synthetic ID from the real backend. Other runs and ordinary viewer URLs retain their current behavior.

Share the filesystem run middleware between Vite dev and preview so the production-preview browser regression can load the real reference bundle and its three source images. Keep existing shim tests and assertions. Add Vitest coverage for the reported empty-list case and a Playwright test that builds and opens the exact URL through Vite preview, without API mocks and with an origin-only Referer. The test must stop its preview in a finally block. Kerege runs it; this sandbox does not start servers or capture screenshots.

Verification: lint, typecheck, all 817 Vitest tests in 44 files and the production build passed. Confirmed the reference data exists in the compiled client. Tests cover empty and unsupported APIs, numeric and first deep links, real API precedence, and visible 401/403/503 failures. Existing assertions remain unchanged. Vite's installed MIT licence was checked for the programmatic preview test. Compared the card structure with finding-light.png: existing qv-detail, qv-sev, qv-facts3, the large frame and selected thumbnail strip remain in use, with qv-mask polygons; no design values changed. Pixel comparison and the actual compiled-preview browser run belong to Kerege/tester; no servers or screenshots were started here.

## Compiled desktop loading repair

The compiled-preview assertion timed out on desktop while both phone variants passed. Browser failure artifacts are unavailable locally. The current loading path serializes evidence behind all geometry downloads, and changing API findings recreates the WebGL engine. Start evidence loading alongside the bundle, show its existing panel while geometry is pending, and hand off the same Query cache to the ready viewer. Update finding attributes in the existing engine instead of recreating its scene. Add deferred-geometry and scene-retention regressions. Preserve the compiled-preview assertions and their timeouts; Kerege runs browsers.

Verification: lint and typecheck passed; all 822 Vitest tests in 44 files and the production build passed. New tests hold geometry unresolved while asserting the loaded class, three frames and polygons, then check the handoff and dismissal. Engine tests verify in-place finding updates/clears, current picking IDs, stable attributes and unchanged camera. The compiled-preview test file, assertions and timeouts are unchanged. No server, browser run or screenshot was started in this sandbox; Kerege must confirm the reported desktop failures are resolved.
