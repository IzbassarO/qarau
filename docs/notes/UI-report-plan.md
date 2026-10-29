# UI-report plan

Continue this branch; preserve tests and fixture assertions.

- Authenticated PDF, ru/kk/en, X-Report-SHA256, API error detail and 202 polling.
- API summary: object/date, findings/status/deciders, scale, COV1-b percentages
  and not-seen regions; 404/computed=false means coverage not computed.
- Shared iframe/download Blob URL, revoke on change/unmount, aria-busy until ready.
- Done-list/viewer links, API-resolved demo alias, v8 desktop/phone layout and i18n.
- Consolidate duplicated rendering, lookups and test setup to reduce review size.

Keep the Node 26 byte-response fix: https://github.com/nodejs/node/issues/66870.
No new dependencies. MIT: React/router/query/Vite/Vitest/Node; Apache-2.0:
Playwright; OFL: fonts. All allow commercial use.

Check lint, types, Vitest, build, diff and e2e discovery. Kerege runs browsers and
captures /inspections/demo/report: light/dark, ru/kk/en, 1440x900 and 390x844,
waiting for aria-busy=false and Blob links. No local servers.

Passed: lint, types, 905 tests, build, diff-check; 30 report e2e cases discovered.
All 378 matcher calls/expected arguments in edited tests are preserved.
