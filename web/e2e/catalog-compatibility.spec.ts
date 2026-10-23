import { expect, test } from '@playwright/test';
import en from '../src/i18n/en.json' with { type: 'json' };
import ru from '../src/i18n/ru.json' with { type: 'json' };
import kk from '../src/i18n/kk.json' with { type: 'json' };
import catalog from '../src/test/fixtures/run-catalog.json' with { type: 'json' };

// The orchestrator starts Vite's read-only run catalog, without FastAPI CRUD routes.
for (const theme of ['light', 'dark'] as const) {
  for (const [lang, t] of Object.entries({ en, ru, kk })) {
    test(`read-only catalog shows statuses and opens a calm upload dialog: ${lang}, ${theme}`, async ({
      page,
    }) => {
      await page.emulateMedia({ colorScheme: theme });
      await page.addInitScript((locale) => {
        localStorage.setItem('qarau.lang', locale);
        sessionStorage.setItem('qarau.auth.session', 'Bearer test-api-value');
      }, lang);
      await page.route('**/api/**', (route) => {
        expect(route.request().headers().authorization).toBe('Bearer test-api-value');
        if (new URL(route.request().url()).pathname === '/api/runs')
          return route.fulfill({ json: catalog });
        return route.fulfill({
          status: 200,
          contentType: 'text/html',
          body: '<!doctype html><html></html>',
        });
      });
      await page.goto('/');
      const navigation = page.getByRole('navigation');
      await expect(navigation.locator('.inspection-status-chip')).toHaveCount(3);
      await expect(
        navigation
          .getByRole('link', { name: /Walkdown upload/ })
          .getByText(t['status.uploaded'], { exact: true }),
      ).toBeVisible();
      await expect(
        navigation
          .getByRole('link', { name: /Failed survey/ })
          .getByText(t['status.failed'], { exact: true }),
      ).toBeVisible();
      await expect(
        navigation
          .getByRole('link', { name: /Legacy export/ })
          .getByText(t['status.unknown'], { exact: true }),
      ).toBeVisible();
      for (const chip of await navigation.locator('.inspection-status-chip').all())
        expect(await chip.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(
          true,
        );
      await expect(page.getByRole('alert')).toHaveCount(0);
      await navigation.getByRole('button', { name: t['inspection.new'] }).click();
      const dialog = page.getByRole('dialog', { name: t['inspection.new'] });
      await expect(dialog).toBeVisible();
      await expect(dialog.getByLabel(t['upload.site'])).toBeVisible();
      await expect(
        dialog.getByRole('option', { name: t['upload.sitesUnavailable'] }),
      ).toBeAttached();
      await expect(dialog.getByText(t['upload.catalogOnly'])).toBeVisible();
      await expect(dialog.locator('input[type="file"]')).toBeAttached();
      await expect(page.getByRole('alert')).toHaveCount(0);
      await expect(dialog.getByRole('button', { name: t['upload.start'] })).toBeDisabled();
      await page.keyboard.press('Escape');
      await page.goto('/?new-inspection');
      await expect(dialog).toBeVisible();
      await expect(dialog.getByText(t['upload.catalogOnly'])).toBeVisible();
      await expect(page.getByRole('alert')).toHaveCount(0);
    });
  }
}

test('site retry remains readable on a narrow dialog and still recovers from a real failure', async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.addInitScript(() => {
    localStorage.setItem('qarau.lang', 'ru');
    sessionStorage.setItem('qarau.auth.session', 'Bearer test-api-value');
  });
  let sitesAvailable = false;
  await page.route('**/api/inspections', (route) => route.fulfill({ json: [] }));
  await page.route('**/api/sites', (route) =>
    sitesAvailable
      ? route.fulfill({ json: [{ id: 1, org_id: 1, name: 'North site' }] })
      : route.fulfill({ status: 503 }),
  );
  await page.goto('/?new-inspection');
  const dialog = page.getByRole('dialog');
  await expect(dialog.getByRole('alert')).toHaveText(ru['upload.sitesError']);
  const retry = dialog.getByRole('button', { name: ru['shell.retry'] });
  await expect(retry).toHaveCSS('white-space', 'nowrap');
  expect(await retry.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
  sitesAvailable = true;
  await retry.click();
  await expect(dialog.getByRole('option', { name: 'North site' })).toBeAttached();
  await expect(dialog.getByRole('alert')).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});
