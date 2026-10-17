import { expect, test } from '@playwright/test';
import en from '../src/i18n/en.json' with { type: 'json' };
import ru from '../src/i18n/ru.json' with { type: 'json' };
import kk from '../src/i18n/kk.json' with { type: 'json' };

for (const [lang, t] of Object.entries({ en, ru, kk })) {
  test(`control center default processing demo: ${lang}`, async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await page.addInitScript((locale) => {
      localStorage.setItem('qarau.lang', locale);
      sessionStorage.setItem('qarau.auth.session', 'Bearer test-api-value');
    }, lang);
    await page.route('**/api/runs', (route) => route.fulfill({ status: 503, body: '' }));
    await page.goto('/inspections/demo');
    await expect(page.getByRole('heading', { name: t['demo.inspectionTitle'] })).toBeVisible();
    await expect(
      page.getByText(t['processing.error.tooFewRegisteredFrames.whatToDo']),
    ).toBeVisible();
    const steps = page.getByRole('list', { name: t['inspection.processing'] });
    for (const [index, state] of ['done', 'done', 'failed', 'skipped'].entries())
      await expect(steps.locator('li').nth(index)).toHaveAttribute('data-state', state);
    await expect(steps.getByText(t['processing.status.skipped'])).toBeVisible();
    await expect(
      page
        .getByRole('navigation')
        .getByRole('link', { name: new RegExp(t['demo.inspectionTitle']) }),
    ).toHaveAttribute('aria-current', 'page');

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
    expect(errors).toEqual([]);
  });
}

test('home lists the real runs, not the demo', async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('qarau.lang', 'en');
    sessionStorage.setItem('qarau.auth.session', 'Bearer test-api-value');
  });
  await page.route('**/api/runs', (route) =>
    route.fulfill({ json: [{ run_id: 'notre-dame', source: { name: 'notre-dame.mp4' } }] }),
  );
  await page.goto('/');
  const navigation = page.getByRole('navigation');
  await expect(navigation.getByRole('link', { name: /notre-dame\.mp4/ })).toBeVisible();
  await expect(navigation.getByRole('link', { name: /Tank farm survey/ })).toHaveCount(0);
});

test('mobile sidebar collapses, opens and closes with navigation', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.addInitScript(() => {
    localStorage.setItem('qarau.lang', 'en');
    sessionStorage.setItem('qarau.auth.session', 'Bearer test-api-value');
  });
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
