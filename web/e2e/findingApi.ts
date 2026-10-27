import type { Page } from '@playwright/test';
import fixture from '../src/test/fixtures/finding.json' with { type: 'json' };

/** Test-only DEF1 responses; product code always uses the authenticated backend. */
export async function mockFindingApi(page: Page) {
  await page.route('**/api/inspections', (route) =>
    route.fulfill({
      json: [
        {
          id: 41,
          site_id: 1,
          mode: 'quick_scan',
          status: 'done',
          created_at: '2026-10-21T10:00:00Z',
        },
      ],
    }),
  );
  await page.route('**/api/inspections/41/runs', (route) =>
    route.fulfill({
      json: [
        {
          id: 'fixture-notre-dame-run',
          inspection_id: 41,
          run_dir: 'notre-dame-p3e',
          status: 'done',
          progress: 100,
        },
      ],
    }),
  );
  await page.route('**/api/inspections/41/findings', (route) =>
    route.fulfill({ json: [fixture.finding] }),
  );
  await page.route('**/api/findings/701', (route) => route.fulfill({ json: fixture.finding }));
}
