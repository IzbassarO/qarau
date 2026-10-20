import { expect, test } from '@playwright/test';
import en from '../src/i18n/en.json' with { type: 'json' };
import ru from '../src/i18n/ru.json' with { type: 'json' };
import kk from '../src/i18n/kk.json' with { type: 'json' };
import tokens from '../src/design/tokens.json' with { type: 'json' };

for (const theme of ['light', 'dark'] as const) {
  for (const [lang, t] of Object.entries({ en, ru, kk })) {
    test(`signed-in home uses ${theme} tokens and fits labels: ${lang}`, async ({ page }) => {
      await page.emulateMedia({ colorScheme: theme });
      await page.addInitScript((locale) => {
        localStorage.setItem('qarau.lang', locale);
        sessionStorage.setItem('qarau.auth.session', 'Bearer test-api-value');
      }, lang);
      await page.route('**/api/runs', (route) =>
        route.fulfill({
          json: [
            {
              run_id: 'tank-survey',
              title: t['demo.inspectionTitle'],
              site: { id: 'tank-farm', name: t['demo.siteName'] },
              created_at: '2026-10-16T15:00:00Z',
            },
          ],
        }),
      );
      await page.goto('/');
      const rgb = (hex: string) =>
        `rgb(${[1, 3, 5].map((start) => parseInt(hex.slice(start, start + 2), 16)).join(', ')})`;
      await expect(page.locator('body')).toHaveCSS('background-color', rgb(tokens[theme].bg));
      const navigation = page.getByRole('navigation', { name: t['shell.sites'] });
      await expect(navigation).toBeVisible();
      await expect(page.locator('.sidebar-container')).toHaveCSS(
        'background-color',
        rgb(tokens[theme].surface),
      );
      await expect(
        page.getByRole('main').getByRole('heading', { name: t['shell.emptyTitle'], exact: true }),
      ).toBeVisible();
      await expect(navigation.getByRole('button', { name: t['inspection.new'] })).toBeVisible();
      await expect(
        navigation.getByRole('link', { name: new RegExp(t['demo.inspectionTitle']) }),
      ).toHaveAttribute('href', '/inspections/tank-survey');
      await expect(navigation.getByRole('button', { name: t['auth.signOut'] })).toBeVisible();
      for (const label of await page
        .locator(
          '.shell-button, .site-name, .inspection-link-title, .sidebar-heading h2, .empty-state h1',
        )
        .all()) {
        const size = await label.evaluate((element) => ({
          text: element.textContent,
          className: element.className,
          scrollWidth: element.scrollWidth,
          clientWidth: element.clientWidth,
        }));
        expect(size.scrollWidth, JSON.stringify(size)).toBeLessThanOrEqual(size.clientWidth);
      }
      expect(
        await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
      ).toBe(true);
    });
  }
}

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
    await expect(page.locator('canvas').locator('..')).toHaveCSS(
      'background-color',
      'rgb(0, 0, 0)',
    );
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
    route.fulfill({
      json: [
        {
          run_id: 'notre-dame',
          source: { name: 'notre_dame.webm' },
          created_at: '2026-10-16T15:00:00Z',
        },
      ],
    }),
  );
  await page.goto('/');
  const navigation = page.getByRole('navigation');
  const inspection = navigation.getByRole('link', { name: /^notre-dame/ });
  await expect(inspection.locator('.inspection-link-name')).toHaveText('notre-dame');
  await expect(inspection.getByRole('time')).toHaveText('16 Oct');
  await expect(inspection.getByRole('time')).toHaveAttribute('datetime', '2026-10-16T15:00:00Z');
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
