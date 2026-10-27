# UI-evidence — implementation plan

Only part 1 is implemented in this round. Backend files and design sources are read-only.

1. Mirror DEF1 and REV1 JSON contracts, enums and session roles in a typed client using the existing API transport and TanStack Query keys. DEF1 lists by inspection, so resolve a run through inspection metadata and filter by the exact API run ID. Keep unsupported legacy catalogs usable without presenting fabricated findings as real observations.
2. Add pure contain-fit and pixel polygon transforms with fixed numeric expectations. Format area from the API measurement and scale source, with translated units and an explicit missing-scale label.
3. Build the read-only evidence card using existing v8 detail, thumbnail and mask components. Show loading, readable failure, empty evidence, class, severity, effective status, area and scale source. Load protected frames through SessionImage; use natural image dimensions for SVG alignment. Never display confidence.
4. Connect API findings to map point indices and markers, preserving bundle finding IDs. Support `?finding=<id>` and `?finding=first`; opening evidence replaces point info. Add an explicitly dev-only fixture response for the requested notre-dame-p3e inspection page, using the checked-in fixture. Production always uses the backend.
5. Test fetch URLs, authenticated decision POST bodies, fixture parsing, panel states, outline coordinates, area text and viewer opening/picking with mocked APIs. Run lint, typecheck, all Vitest tests and production build. No screenshot capture or server is needed; Kerege runs browser checks.

Parts 2 and 3 remain separate: append-only decision log and role-based actions; broader locale/phone validation and Playwright fixture e2e. New part-1 copy still uses the existing ru/kk/en dictionaries to avoid hard-coded interface text.

References: `backend/app/findings.py`, `decisions.py`, `auth.py`, model enums and OpenAPI tests; `docs/design/DESIGN.md`, `viewer-3d.md`, `screens/finding-light.png` and `screens/evidence-phone-light.png`.

Licences checked in installed package metadata: React, Three.js, TanStack Query, Vitest and Testing Library are MIT, permitting commercial use. Existing Playwright is Apache-2.0 per the task contract. No dependencies or models are added.

OpenAPI verification: the full app import needs unavailable `rq`. Using the existing backend and pipeline virtual environments, generated OpenAPI directly from the unmodified findings, decisions and CRUD routers in a temporary in-memory FastAPI app. Verified FindingOut, EvidenceOut, DecisionIn/Out, RunOut and every enum; no dependency installation, backend edits or server.

The local fixture preview is opt-in: the Vite API stand-in supplies the checked-in fixture only for requests originating from `/viewer/notre-dame-p3e?finding=…`, when that bundle exists. Other screens, proxied APIs and production builds use their existing data sources.

## Part 1 verification

- `cd web && pnpm lint && pnpm typecheck` — passed.
- `cd web && pnpm test && pnpm build` — passed, 43 test files and 801 tests. The build retains Vite's large-chunk advisory; no build errors.
- Literal contain-fit coordinates, scaled/unscaled area, fetch URLs/session headers, decision POST payloads, loading/error/retry/empty/loaded panel states, hidden confidence, natural image dimensions and resize, large finding IDs and point picking, marker selection and both deep links are covered.
- All three fixture image files exist in the read-only notre-dame-p3e bundle. Preview middleware is covered without starting a server.
- No servers, screenshots or Playwright runs were started. Kerege performs browser verification; full fixture e2e and phone work remain in part 3. Decision log/actions remain in part 2.
