# UI-inspections, part 1

Implement only the inspection data adapter and reusable v8 list rows. Start-page wiring,
navigation/retry actions, demo route, phone composition and Playwright belong to part 2.

1. Read the project rules, v8 screens/components and B1/S1 schemas.
2. Preserve the grouped legacy catalog and its callers. Add a strict flat summary, pure
   newest-first sorting/capping and a session-authenticated TanStack Query hook.
3. Build ListRow/StatusText/secondary-button markup from the existing DS1 bundle, with
   translated headers, statuses, actions, count and first-run copy in en/ru/kk.
4. Add literal API fixtures and Vitest coverage for all states, callbacks, locale dates,
   ordering, empty data, the 50-row cap and API/session behavior.
5. Run lint, typecheck, all Vitest tests and build. No server or screenshots.

Licences checked in installed package metadata: React, React DOM, TanStack Query, Vite,
Vitest and Testing Library are MIT; TypeScript is Apache-2.0. Existing DS1 fonts are OFL
per DESIGN.md. All permit commercial use. No new dependencies or models.
