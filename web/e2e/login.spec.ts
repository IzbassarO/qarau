import { openNavigation } from './chrome';
import { expect, test, type Page } from '@playwright/test';
import en from '../src/i18n/en.json' with { type: 'json' };
import ru from '../src/i18n/ru.json' with { type: 'json' };
import kk from '../src/i18n/kk.json' with { type: 'json' };

const themeColors = {
  light: {
    bg: 'rgb(244, 244, 243)',
    surface: 'rgb(255, 255, 255)',
    accent: 'rgb(17, 17, 17)',
  },
  dark: {
    bg: 'rgb(22, 22, 22)',
    surface: 'rgb(30, 30, 30)',
    accent: 'rgb(242, 242, 240)',
  },
};

async function expectHeroGradient(page: Page) {
  const hero = page.locator('.login-hero');
  await expect(hero).toHaveCSS('background-color', 'rgb(18, 18, 18)');
  await expect(hero).toHaveCSS('color', 'rgb(244, 244, 242)');
  await expect(hero.locator('.qv-mark--hero')).toHaveCSS('background-color', 'rgb(255, 255, 255)');
  await expect(hero.locator('.qv-mark--hero')).toHaveCSS('color', 'rgb(17, 17, 17)');
  // Fixed v8 export fixture: also check the pseudo-element paints, since a
  // background-image alone can still be hidden by a phone display:none rule.
  expect(
    await hero.evaluate((element) => {
      const style = getComputedStyle(element, '::before');
      return {
        display: style.display,
        visibility: style.visibility,
        opacity: style.opacity,
        content: style.content,
        image: style.backgroundImage,
      };
    }),
  ).toEqual({
    display: 'block',
    visibility: 'visible',
    opacity: '1',
    content: '""',
    image:
      'radial-gradient(900px 640px at 20% 0%, rgb(58, 58, 58) 0%, rgba(58, 58, 58, 0) 60%), radial-gradient(700px 500px at 100% 100%, rgb(42, 42, 42) 0%, rgba(42, 42, 42, 0) 60%)',
  });
}

for (const theme of ['light', 'dark'] as const) {
  for (const [lang, t] of Object.entries({ en, ru, kk })) {
    test(`signed-out ${theme} login: ${lang}`, async ({ page }) => {
      await page.emulateMedia({ colorScheme: theme });
      await page.addInitScript((locale) => localStorage.setItem('qarau.lang', locale), lang);
      await page.goto('/?signed-out');
      await expectHeroGradient(page);
      await expect(page.getByRole('heading', { name: t['auth.title'] })).toBeVisible();
      await expect(page.getByLabel(t['auth.username'])).toBeVisible();
      await expect(page.getByLabel(t['auth.password'])).toHaveAttribute('type', 'password');
      await expect(page.getByRole('button', { name: t['auth.signIn'] })).toBeVisible();
      await expect(page.getByRole('navigation')).toHaveCount(0);
      await expect(page.getByRole('alert')).toHaveCount(0);
      await expect(page.locator('.login-page')).toHaveCSS(
        'background-color',
        themeColors[theme].bg,
      );
      await expect(page.locator('.login-card')).toHaveCSS(
        'background-color',
        page.viewportSize()!.width === 1440 ? themeColors[theme].surface : 'rgba(0, 0, 0, 0)',
      );
      await expect(page.locator('.shell-wordmark')).toHaveText(t['shell.product']);
      await expect(page.getByRole('button', { name: t['auth.signIn'] })).toHaveCSS(
        'background-color',
        themeColors[theme].accent,
      );
      const card = await page.locator('.login-card').boundingBox();
      expect(card).not.toBeNull();
      if (page.viewportSize()!.width === 1440) {
        // v8: the form is centered in the right half of the 1440 px desktop layout.
        expect(Math.abs(card!.x + card!.width / 2 - 1080)).toBeLessThanOrEqual(1);
        await expect(page.locator('.qv-hero')).toBeVisible();
        await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
        await expect(page.locator('.login-page')).toHaveCSS('display', 'grid');
        await expect(page.locator('.login-page')).toHaveCSS('grid-template-columns', '720px 720px');
        await expect(page.locator('.login-content')).toHaveCSS('place-items', 'center');
        const hero = await page.locator('.qv-hero').boundingBox();
        const content = await page.locator('.login-content').boundingBox();
        expect(hero).not.toBeNull();
        expect(content).not.toBeNull();
        expect(hero!.x).toBe(0);
        expect(hero!.width).toBe(720);
        expect(content!.x).toBe(720);
        expect(content!.width).toBe(720);
      } else {
        expect(Math.abs(card!.x + card!.width / 2 - 195)).toBeLessThanOrEqual(1);
        await expect(page.locator('.login-page')).toHaveCSS('grid-template-columns', '390px');
        await expect(page.locator('.login-hero')).toHaveCSS('height', '56px');
        await expect(page.locator('.login-hero-copy')).not.toBeVisible();
        await expect(page.locator('.login-card')).toHaveCSS('background-color', 'rgba(0, 0, 0, 0)');
      }
      await expect(page.locator('.qv-hero .shell-wordmark')).toHaveCount(1);
      await expect(page.locator('.login-card .shell-wordmark')).toHaveCount(0);
      await expect(page.getByRole('button', { name: t['auth.signIn'] })).toHaveClass(
        'qv-btn qv-btn--lg qv-btn--block',
      );
      for (const control of await page.locator('.login-card input, .login-card button').all()) {
        expect(
          await control.evaluate((element) => element.scrollWidth <= element.clientWidth),
        ).toBe(true);
      }
    });
  }
}

for (const theme of ['light', 'dark'] as const) {
  for (const [lang, t] of Object.entries({ en, ru, kk })) {
    test(`signed-out phone uses v8 ${theme} layout: ${lang}`, async ({ page }) => {
      await page.setViewportSize({ width: 390, height: 844 });
      await page.emulateMedia({ colorScheme: theme });
      await page.addInitScript((locale) => localStorage.setItem('qarau.lang', locale), lang);
      await page.goto('/?signed-out');
      await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
      await expect(page.locator('.login-page')).toHaveCSS('display', 'grid');
      await expect(page.locator('.login-page')).toHaveCSS('grid-template-columns', '390px');
      await expect(page.locator('.qv-hero')).toBeVisible();
      await expectHeroGradient(page);
      await expect(page.locator('.qv-hero .shell-wordmark')).toHaveText(t['shell.product']);
      await expect(page.locator('.login-card .shell-wordmark')).toHaveCount(0);
      await expect(page.getByRole('button', { name: t['auth.signIn'] })).toHaveCSS(
        'background-color',
        themeColors[theme].accent,
      );
      await expect(page.getByLabel(t['auth.username'])).toBeVisible();
      await expect(page.getByLabel(t['auth.password'])).toBeVisible();
      await expect(page.getByRole('alert')).toHaveCount(0);
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(390);
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
  for (const path of ['inspections', 'sites'])
    await page.route(`**/api/${path}`, (route) => {
      expect(route.request().headers().authorization).toBe('Bearer test-api-value');
      return route.fulfill({ json: [] });
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
  await openNavigation(page);
  await expect(page.getByRole('navigation')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Inspection not found' })).toBeVisible();
  await expect(page).toHaveURL(/\/inspections\/private-run$/);
  expect(await page.evaluate(() => localStorage.getItem('qarau.auth.session'))).toBeNull();
  await page.reload();
  await openNavigation(page);
  await expect(page.getByRole('navigation')).toBeVisible();
  await page.getByRole('button', { name: 'Sign out' }).click();
  await expect(page.getByRole('button', { name: 'Sign in' })).toBeVisible();
  await expect(page.getByRole('navigation')).toHaveCount(0);
  expect(await page.evaluate(() => sessionStorage.getItem('qarau.auth.session'))).toBeNull();
});

test('keeps the point viewer public on map-ground in the dev server', async ({ page }) => {
  await page.goto('/viewer/demo');
  await expect(page.locator('input[type="password"]')).toHaveCount(0);
  await expect(page.locator('canvas')).toBeVisible();
  await expect(page.locator('canvas').locator('..')).toHaveCSS(
    'background-color',
    'rgb(19, 19, 19)',
  );
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
