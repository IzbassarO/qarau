import { expect, test, type Page } from '@playwright/test';
import en from '../src/i18n/en.json' with { type: 'json' };
import ru from '../src/i18n/ru.json' with { type: 'json' };
import kk from '../src/i18n/kk.json' with { type: 'json' };

async function expectBottomHint(page: Page) {
  const hint = page.getByRole('status');
  await expect(hint).toBeVisible();
  await expect(hint).toBeInViewport();
  await expect(hint).toHaveCSS('bottom', '12px');
  // Geometry is the actual result; the expected no-overlap result is fixed.
  expect(
    await hint.evaluate((element) => {
      const hintBox = element.getBoundingClientRect();
      return [...document.querySelectorAll('.viewer-gizmo, .viewer-minimap')].some((aid) => {
        const box = aid.getBoundingClientRect();
        return (
          box.left < hintBox.right &&
          box.right > hintBox.left &&
          box.top < hintBox.bottom &&
          box.bottom > hintBox.top
        );
      });
    }),
  ).toBe(false);
}

for (const theme of ['light', 'dark'] as const) {
  for (const [lang, t] of Object.entries({ en, ru, kk })) {
    test(`map navigation aids use dark map tokens and leave the bottom hint clear: ${theme}, ${lang}`, async ({
      page,
    }) => {
      await page.emulateMedia({ colorScheme: theme });
      await page.addInitScript((locale) => localStorage.setItem('qarau.lang', locale), lang);
      await page.goto('/viewer/demo');
      await page.waitForFunction(() => window.__qarau?.pointsLoaded);
      await page.evaluate(() => document.fonts.ready);

      const gizmo = page.getByRole('group', { name: t['gizmo.label'] });
      await expect(gizmo).toHaveCSS('background-color', 'rgb(19, 19, 19)');
      await expect(gizmo).toHaveCSS('border-top-color', 'rgb(38, 38, 38)');
      await expect(gizmo).toHaveCSS('color', 'rgb(237, 237, 235)');
      for (const axis of await gizmo.getByRole('button').all()) {
        await expect(axis).toHaveCSS('color', 'rgb(237, 237, 235)');
      }
      await expectBottomHint(page);

      await page.getByRole('button', { name: t['viewer.mode.observer'], exact: true }).click();
      const minimap = page.getByTestId('observer-minimap');
      await expect(minimap).toBeVisible();
      await expect(minimap).toHaveCSS('background-color', 'rgb(19, 19, 19)');
      await expect(minimap).toHaveCSS('border-top-color', 'rgb(38, 38, 38)');
      await expect(minimap.locator('.viewer-minimap-label')).toHaveCSS(
        'background-color',
        'rgb(19, 19, 19)',
      );
      await expect(minimap.locator('.viewer-minimap-label')).toHaveCSS(
        'color',
        'rgb(237, 237, 235)',
      );
      await expectBottomHint(page);
      await expect(page.getByRole('alert')).toHaveCount(0);
    });
  }
}
