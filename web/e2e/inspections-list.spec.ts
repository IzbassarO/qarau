import { expect, test } from '@playwright/test';
import { inspectionsFixture } from '../src/fixtures/inspections';
import { readDenseFixture } from '../src/fixtures/dense-bundle/readFixture';
import en from '../src/i18n/en.json' with { type: 'json' };
import ru from '../src/i18n/ru.json' with { type: 'json' };
import kk from '../src/i18n/kk.json' with { type: 'json' };

// The payload mirrors B1/S1, with optional DEF1 counts and the API's run IDs.
const inspections = inspectionsFixture.map((inspection) => ({
  id: Number(inspection.id),
  site_id: Number(inspection.id),
  mode: 'quick_scan',
  status: inspection.status,
  created_at: inspection.date,
  findings_count: inspection.findingsCount,
  run_id: inspection.runId,
}));
const sites = inspectionsFixture.map((inspection) => ({
  id: Number(inspection.id),
  org_id: 1,
  name: inspection.siteName,
}));

for (const [lang, t] of Object.entries({ en, ru, kk })) {
  for (const theme of ['light', 'dark'] as const) {
    test(`inspection start page and API actions: ${lang}, ${theme}`, async ({ page }) => {
      await page.emulateMedia({ colorScheme: theme });
      await page.addInitScript((locale) => {
        localStorage.setItem('qarau.lang', locale);
        sessionStorage.setItem('qarau.auth.session', 'Bearer test-api-value');
      }, lang);
      let retried = false;
      let listReads = 0;
      const posts: string[] = [];
      const findingsRequests: string[] = [];
      const { manifest, files } = readDenseFixture();
      await page.route('**/api/**', (route) => {
        const request = route.request();
        const path = new URL(request.url()).pathname;
        expect(request.headers().authorization).toBe('Bearer test-api-value');
        if (path === '/api/sites') return route.fulfill({ json: sites });
        if (path === '/api/inspections') {
          listReads++;
          // Deliberately unsorted: the list must restore the fixture chronology.
          return route.fulfill({
            json: inspections
              .toReversed()
              .map((inspection) =>
                inspection.id === 101 && retried ? { ...inspection, status: 'queued' } : inspection,
              ),
          });
        }
        if (path === '/api/inspections/102/findings') {
          expect(request.method()).toBe('GET');
          findingsRequests.push(path);
          return route.fulfill({ json: [] });
        }
        if (path === '/api/inspections/101')
          return route.fulfill({
            json: {
              ...inspections[4],
              steps: [{ name: 'sparse', status: 'failed', error: 'timeout' }],
            },
          });
        if (path === '/api/inspections/103')
          return route.fulfill({
            json: { ...inspections[2], steps: [{ name: 'frames', status: 'running' }] },
          });
        if (path === '/api/inspections/101/runs/inspection-failed/steps/sparse/rerun') {
          expect(request.method()).toBe('POST');
          posts.push(path);
          retried = true;
          return route.fulfill({
            status: 202,
            json: {
              run_id: 'inspection-failed',
              status: 'queued',
              job_id: 'retry-job',
              from_step: 'sparse',
            },
          });
        }
        if (path === '/api/runs/inspection-done/manifest.json')
          return route.fulfill({ json: { ...manifest, run_id: 'inspection-done' } });
        const file = files.get(path.replace('/api/runs/inspection-done/files/', ''));
        if (file)
          return route.fulfill({
            body: file,
            contentType: path.endsWith('.svg') ? 'image/svg+xml' : 'application/octet-stream',
          });
        throw new Error(`Unexpected inspection request: ${request.method()} ${path}`);
      });

      await page.goto('/');
      const main = page.getByRole('main');
      await expect(main.getByRole('heading', { name: t['list.title'] })).toBeVisible();
      const create = main.getByRole('button', { name: t['list.newInspection'] });
      await expect(create).toBeVisible();
      const table = main.getByRole('table', { name: t['list.title'] });
      const rows = table.locator('[data-inspection-id]');
      await expect(rows).toHaveCount(5);
      expect(
        await rows.evaluateAll((elements) =>
          elements.map((row) => row.getAttribute('data-inspection-id')),
        ),
      ).toEqual(['105', '104', '103', '102', '101']);
      await expect(rows.locator('.qv-row__name')).toHaveText([
        'Pipe rack PR-2, bay 7',
        'Tank T-104, north wall',
        'Tank T-112, roof',
        'Separator V-301, shell',
        'Pipe rack PR-5, bays 12 to 14',
      ]);
      await expect(rows.locator('.qv-status')).toHaveText([
        t['list.status.uploaded'],
        t['list.status.queued'],
        t['list.status.running'],
        t['list.status.done'],
        t['list.status.failed'],
      ]);
      await expect(rows.locator('.inspections-list-count')).toHaveText(['—', '—', '2', '4', '—']);
      await expect(rows.getByRole('button')).toHaveText([
        t['list.showProgress'],
        t['list.showProgress'],
        t['list.showProgress'],
        t['list.openMap'],
        t['list.retry'],
      ]);
      await page.evaluate(() => document.fonts.ready);
      for (const button of await rows.getByRole('button').all()) {
        await button.scrollIntoViewIfNeeded();
        await expect(button).toBeVisible();
        await expect(button).toBeInViewport();
        expect(await button.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(
          true,
        );
        if (page.viewportSize()!.width === 390)
          expect((await button.boundingBox())!.height).toBeGreaterThanOrEqual(44);
      }
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
        true,
      );
      await expect(page.getByRole('alert')).toHaveCount(0);

      const beforeRetry = listReads;
      await rows.nth(4).getByRole('button', { name: t['list.retry'] }).click();
      await expect(rows.nth(4).getByText(t['list.status.queued'], { exact: true })).toBeVisible();
      expect(posts).toEqual(['/api/inspections/101/runs/inspection-failed/steps/sparse/rerun']);
      expect(listReads).toBeGreaterThan(beforeRetry);
      await expect(rows.nth(4).getByRole('button', { name: t['list.showProgress'] })).toBeVisible();
      await expect(page.getByRole('alert')).toHaveCount(0);

      await rows.nth(2).getByRole('button', { name: t['list.showProgress'] }).click();
      await expect(page).toHaveURL('/inspections/103');
      await expect(
        page
          .getByRole('list', { name: t['inspection.processing'] })
          .locator('[data-state="running"]'),
      ).toHaveCount(1);

      await page.goto('/');
      await main.getByRole('button', { name: t['list.openMap'] }).click();
      await expect(page).toHaveURL('/viewer/inspection-done');
      await page.waitForFunction(() => window.__qarau?.pointsLoaded);
      await expect.poll(() => findingsRequests).toEqual(['/api/inspections/102/findings']);
      await expect(page.getByRole('alert')).toHaveCount(0);
    });

    test(`inspection demo is fixed and its row actions are inert: ${lang}, ${theme}`, async ({
      page,
    }) => {
      await page.emulateMedia({ colorScheme: theme });
      await page.addInitScript((locale) => {
        localStorage.setItem('qarau.lang', locale);
        sessionStorage.setItem('qarau.auth.session', 'Bearer test-api-value');
      }, lang);
      const requests: string[] = [];
      await page.route('**/api/**', (route) => {
        requests.push(route.request().url());
        return route.fulfill({ status: 503 });
      });
      await page.goto('/?demo=inspections');
      await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
      const rows = page.getByRole('table').locator('[data-inspection-id]');
      await expect(rows).toHaveCount(5);
      await expect(rows.locator('.qv-status')).toHaveText([
        t['list.status.uploaded'],
        t['list.status.queued'],
        t['list.status.running'],
        t['list.status.done'],
        t['list.status.failed'],
      ]);
      await expect(rows.locator('.inspections-list-count')).toHaveText(['—', '—', '2', '4', '—']);
      await expect(rows.getByRole('button')).toHaveText([
        t['list.showProgress'],
        t['list.showProgress'],
        t['list.showProgress'],
        t['list.openMap'],
        t['list.retry'],
      ]);
      for (const button of await rows.getByRole('button').all()) {
        await button.click();
        await expect(page).toHaveURL('/?demo=inspections');
      }
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
        true,
      );
      expect(requests).toEqual([]);
      await expect(page.getByRole('alert')).toHaveCount(0);
    });
  }

  test(`inspection list empty state: ${lang}`, async ({ page }) => {
    await page.addInitScript((locale) => {
      localStorage.setItem('qarau.lang', locale);
      sessionStorage.setItem('qarau.auth.session', 'Bearer test-api-value');
    }, lang);
    await page.route('**/api/**', (route) => route.fulfill({ json: [] }));
    await page.goto('/');
    const main = page.getByRole('main');
    await expect(main.getByText(t['list.emptyTitle'], { exact: true })).toBeVisible();
    await expect(main.getByText(t['list.emptyDescription'], { exact: true })).toBeVisible();
    await expect(main.getByRole('button', { name: t['list.newInspection'] })).toBeVisible();
    await expect(main.getByRole('table')).toHaveCount(0);
    await expect(page.getByRole('alert')).toHaveCount(0);
  });
}
