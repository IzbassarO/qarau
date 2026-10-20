import { expect, test } from '@playwright/test';
import en from '../src/i18n/en.json' with { type: 'json' };
import ru from '../src/i18n/ru.json' with { type: 'json' };
import kk from '../src/i18n/kk.json' with { type: 'json' };
import tokens from '../src/design/tokens.json' with { type: 'json' };

function rgb(hex: string) {
  return `rgb(${[1, 3, 5].map((start) => parseInt(hex.slice(start, start + 2), 16)).join(', ')})`;
}

for (const theme of ['light', 'dark'] as const) {
  for (const [lang, t] of Object.entries({ en, ru, kk })) {
    test(`signed-out ${theme} login: ${lang}`, async ({ page }) => {
      await page.emulateMedia({ colorScheme: theme });
      await page.addInitScript((locale) => localStorage.setItem('qarau.lang', locale), lang);
      await page.goto('/?signed-out');
      await expect(page.getByRole('heading', { name: t['auth.title'] })).toBeVisible();
      await expect(page.getByLabel(t['auth.username'])).toBeVisible();
      await expect(page.getByLabel(t['auth.password'])).toHaveAttribute('type', 'password');
      await expect(page.getByRole('button', { name: t['auth.signIn'] })).toBeVisible();
      await expect(page.getByRole('navigation')).toHaveCount(0);
      await expect(page.getByRole('alert')).toHaveCount(0);
      await expect(page.locator('.login-page')).toHaveCSS(
        'background-color',
        rgb(tokens[theme].bg),
      );
      await expect(page.locator('.login-card')).toHaveCSS(
        'background-color',
        rgb(tokens[theme].surface),
      );
      await expect(page.locator('.shell-wordmark')).toHaveText(t['shell.product']);
      await expect(page.getByRole('button', { name: t['auth.signIn'] })).toHaveCSS(
        'background-color',
        rgb(tokens[theme].accent),
      );
      const card = await page.locator('.login-card').boundingBox();
      expect(card).not.toBeNull();
      expect(Math.abs(card!.x + card!.width / 2 - 720)).toBeLessThanOrEqual(1);
      for (const control of await page.locator('.login-card input, .login-card button').all()) {
        expect(
          await control.evaluate((element) => element.scrollWidth <= element.clientWidth),
        ).toBe(true);
      }
    });
  }
}

test('rejects bad credentials, signs in, restores the session and signs out', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('qarau.lang', 'en'));
  let accepted = false;
  await page.route('**/api/runs', (route) => {
    expect(route.request().headers().authorization).toBe('Bearer test-api-value');
    return route.fulfill({ status: accepted ? 200 : 401, json: [] });
  });
  await page.goto('/inspections/private-run');
  await page.getByLabel('Username').fill('test-operator');
  await page.getByLabel('Password').fill('test-api-value');
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page.getByRole('alert')).toHaveText(en['auth.invalidCredentials']);
  expect(await page.evaluate(() => sessionStorage.length)).toBe(0);
  accepted = true;
  await page.getByLabel('Password').fill('test-api-value');
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page.getByRole('navigation')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Inspection not found' })).toBeVisible();
  await expect(page).toHaveURL(/\/inspections\/private-run$/);
  expect(await page.evaluate(() => localStorage.getItem('qarau.auth.session'))).toBeNull();
  await page.reload();
  await expect(page.getByRole('navigation')).toBeVisible();
  await page.getByRole('button', { name: 'Sign out' }).click();
  await expect(page.getByRole('button', { name: 'Sign in' })).toBeVisible();
  await expect(page.getByRole('navigation')).toHaveCount(0);
  expect(await page.evaluate(() => sessionStorage.getItem('qarau.auth.session'))).toBeNull();
});

test('keeps the black point viewer public in the dev server', async ({ page }) => {
  await page.goto('/viewer/demo');
  await expect(page.locator('input[type="password"]')).toHaveCount(0);
  await expect(page.locator('canvas')).toBeVisible();
  await expect(page.locator('canvas').locator('..')).toHaveCSS('background-color', 'rgb(0, 0, 0)');
  await page.waitForFunction(() => window.__qarau?.pointsLoaded);
});

test('fits the login form on a narrow screen', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');
  await expect(page.locator('input[type="password"]')).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  );
});
