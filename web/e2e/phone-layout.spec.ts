import { expect, test, type Page } from '@playwright/test';
import { openDetails, openNavigation } from './chrome';
import { inspectionsFixture } from '../src/fixtures/inspections';
import en from '../src/i18n/en.json' with { type: 'json' };
import ru from '../src/i18n/ru.json' with { type: 'json' };
import kk from '../src/i18n/kk.json' with { type: 'json' };

// This regression always exercises the reference phone size, in either project.
test.use({ viewport: { width: 390, height: 844 } });

async function noHorizontalOverflow(page: Page) {
  await page.evaluate(() => document.fonts.ready);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
}

for (const theme of ['light', 'dark'] as const) {
  for (const [lang, t] of Object.entries({ en, ru, kk })) {
    test(`phone / fits its catalog, navigation and upload dialog: ${theme}, ${lang}`, async ({
      page,
    }) => {
      await page.emulateMedia({ colorScheme: theme });
      await page.addInitScript((locale) => {
        localStorage.setItem('qarau.lang', locale);
        sessionStorage.setItem('qarau.auth.session', 'Bearer test-api-value');
      }, lang);
      await page.route('**/api/inspections', (route) =>
        route.fulfill({
          json: inspectionsFixture.slice(0, 2).map((inspection) => ({
            id: Number(inspection.id),
            site_id: 1,
            mode: 'quick_scan',
            status: inspection.status,
            created_at: inspection.date,
            run_id: inspection.runId,
          })),
        }),
      );
      await page.route('**/api/sites', (route) =>
        route.fulfill({ json: [{ id: 1, org_id: 1, name: 'North site' }] }),
      );

      await page.goto('/');
      await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
      await expect(page.getByRole('heading', { name: t['list.title'] })).toBeVisible();
      const rows = page.getByRole('table').locator('[data-inspection-id]');
      await expect(rows).toHaveCount(2);
      await noHorizontalOverflow(page);
      for (const row of await rows.all()) {
        await expect(row).toBeInViewport();
        await expect(row.getByRole('button', { name: t['list.showProgress'] })).toBeVisible();
        expect(await row.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(
          true,
        );
      }

      const navigation = page.getByRole('navigation', { name: t['shell.sites'] });
      await expect(navigation).not.toBeVisible();
      await openNavigation(page);
      await expect(navigation).toBeVisible();
      await noHorizontalOverflow(page);
      await navigation.getByRole('button', { name: t['inspection.new'] }).click();
      const dialog = page.getByRole('dialog', { name: t['inspection.new'] });
      await expect(dialog).toBeVisible();
      await expect(dialog.getByRole('option', { name: 'North site' })).toBeAttached();
      await noHorizontalOverflow(page);
      expect(await dialog.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(
        true,
      );
      await dialog.getByRole('button', { name: t['dialog.close'], exact: true }).last().click();
      await expect(dialog).not.toBeVisible();
      await expect(page.getByRole('alert')).toHaveCount(0);
    });
  }

  test(`phone /viewer/notre-dame-p3e fits the map and open panels: ${theme}`, async ({ page }) => {
    await page.emulateMedia({ colorScheme: theme });
    await page.addInitScript(() => localStorage.setItem('qarau.lang', 'en'));
    await page.goto('/viewer/notre-dame-p3e');
    await page.waitForFunction(() => window.__qarau?.pointsLoaded);
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
    await expect(page.locator('.qv-map')).toHaveCSS('background-color', 'rgb(19, 19, 19)');
    await expect(page.getByRole('alert')).toHaveCount(0);
    await noHorizontalOverflow(page);

    await openDetails(page);
    const panel = page.locator('.viewer-panel');
    await expect(panel).toBeVisible();
    await noHorizontalOverflow(page);
    expect(await panel.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(
      true,
    );
    await page.locator('.viewer-settings > summary').click();
    await expect(panel).not.toBeVisible();
    await expect(page.locator('.viewer-settings-panel')).toBeVisible();
    await noHorizontalOverflow(page);
  });
}
