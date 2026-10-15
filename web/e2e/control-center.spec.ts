import path from 'node:path';
import { expect, test } from '@playwright/test';
import en from '../src/i18n/en.json';
import ru from '../src/i18n/ru.json';

const screenshotDir = process.env.QARAU_SCREENSHOT_DIR ?? 'test-results/a1a-control-center';

for (const [lang, t] of Object.entries({ en, ru })) {
  test(`control center and demo screenshots: ${lang}`, async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await page.addInitScript((locale) => localStorage.setItem('qarau.lang', locale), lang);
    await page.route('**/api/runs', (route) => route.fulfill({ status: 503, body: '' }));
    await page.goto('/');
    await expect(page.getByRole('alert')).toContainText(t['shell.connectionError']);
    await expect(page.getByRole('heading', { name: t['shell.emptyTitle'] })).toBeVisible();
    await expect(page.getByText(t['demo.inspectionTitle'])).toHaveCount(0);
    await page.evaluate(() => document.fonts.ready);
    await page.screenshot({ path: path.join(screenshotDir, `home-${lang}.png`), fullPage: true });

    await page.getByRole('navigation').getByRole('button', { name: t['inspection.new'] }).click();
    const dialog = page.getByRole('dialog');
    await expect(dialog).toBeVisible();
    await dialog.locator('input[type="file"]').setInputFiles({
      name: 'notes.txt',
      mimeType: 'text/plain',
      buffer: Buffer.from('not a video'),
    });
    await expect(dialog.getByRole('alert')).toHaveText(t['upload.invalidType']);
    await expect(dialog.getByText(t['upload.unavailable'])).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(dialog).not.toBeVisible();

    await page.goto('/inspections/demo');
    await expect(page.getByRole('heading', { name: t['demo.inspectionTitle'] })).toBeVisible();
    await expect(page.getByText(t['demo.notice'])).toBeVisible();
    await page.waitForFunction(() => window.__qarau?.pointsLoaded);
    await page.evaluate(() => document.fonts.ready);
    await page.evaluate(
      () =>
        new Promise<void>((resolve) =>
          requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
        ),
    );
    await page.screenshot({ path: path.join(screenshotDir, `demo-${lang}.png`), fullPage: true });
    expect(errors).toEqual([]);
  });
}

test('mobile sidebar collapses, opens and closes with navigation', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.addInitScript(() => localStorage.setItem('qarau.lang', 'en'));
  await page.route('**/api/runs', (route) => route.fulfill({ json: [] }));
  await page.goto('/inspections/demo');
  const navigation = page.getByRole('navigation');
  await expect(navigation).not.toBeVisible();
  await page.getByRole('button', { name: en['shell.openSidebar'] }).click();
  await expect(navigation).toBeVisible();
  await navigation.getByRole('link', { name: /Tank farm survey/ }).click();
  await expect(navigation).not.toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  );
});
