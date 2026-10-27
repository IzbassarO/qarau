import { expect, test, type Locator } from '@playwright/test';
import { closeTools, openDetails, openTools } from './chrome';
import en from '../src/i18n/en.json' with { type: 'json' };
import ru from '../src/i18n/ru.json' with { type: 'json' };
import kk from '../src/i18n/kk.json' with { type: 'json' };

async function fits(locator: Locator) {
  for (const control of await locator.all()) {
    const size = await control.evaluate((element) => ({
      text: element.textContent,
      content: element.scrollWidth,
      width: element.clientWidth,
    }));
    expect(size.content, JSON.stringify(size)).toBeLessThanOrEqual(size.width);
    await expect(control).toBeInViewport();
  }
}

for (const theme of ['light', 'dark'] as const) {
  for (const [lang, t] of Object.entries({ en, ru, kk })) {
    test(`v8 viewer chrome fits and its panels remain usable: ${theme}, ${lang}`, async ({
      page,
    }) => {
      await page.emulateMedia({ colorScheme: theme });
      await page.addInitScript((locale) => localStorage.setItem('qarau.lang', locale), lang);
      await page.goto('/viewer/demo');
      await page.waitForFunction(() => window.__qarau?.pointsLoaded);
      await page.evaluate(() => document.fonts.ready);
      await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
      await expect(page.getByRole('alert')).toHaveCount(0);
      await expect(page.locator('.qv-map')).toHaveCSS('background-color', 'rgb(19, 19, 19)');
      await expect(page.locator('.viewer-screen')).toHaveCSS(
        'color',
        theme === 'light' ? 'rgb(17, 17, 17)' : 'rgb(242, 242, 240)',
      );
      await expect(page.locator('.viewer-screen')).toHaveCSS(
        'font-family',
        '"IBM Plex Sans", system-ui, sans-serif',
      );
      await expect(page.locator('.viewer-run-id')).toHaveCSS(
        'font-family',
        '"JetBrains Mono", ui-monospace, Menlo, monospace',
      );
      await expect(page.locator('.viewer-chrome > details > summary')).toHaveCount(2);
      await fits(page.locator('.viewer-modebar button, .viewer-chrome > details > summary'));
      // The initial pills and navigation modes have distinct hit targets in every language.
      for (const control of await page
        .locator(
          '.viewer-segments button:enabled, .viewer-chrome > details > summary, .viewer-settings > summary',
        )
        .all()) {
        expect(
          await control.evaluate((element) => {
            const box = element.getBoundingClientRect();
            return element.contains(
              document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2),
            );
          }),
        ).toBe(true);
      }
      await openTools(page);
      expect(
        await page.locator('.viewer-toolbar').evaluate((panel) => {
          const top = panel.getBoundingClientRect().top;
          return [...document.querySelectorAll('.viewer-chrome summary, .viewer-segments')].every(
            (control) => control.getBoundingClientRect().bottom <= top,
          );
        }),
      ).toBe(true);
      const reset = page.getByRole('button', { name: t['toolbar.reset'], exact: true });
      await reset.click();
      await closeTools(page);
      await openDetails(page);
      await expect(page.locator('.viewer-panel')).toBeVisible();
      expect(
        await page.locator('.viewer-panel').evaluate((panel) => {
          const top = panel.getBoundingClientRect().top;
          return [...document.querySelectorAll('.viewer-chrome summary, .viewer-segments')].every(
            (control) => control.getBoundingClientRect().bottom <= top,
          );
        }),
      ).toBe(true);
      const toggle = page.getByRole('button', { name: t['show.current'], exact: true });
      const wasPressed = await toggle.getAttribute('aria-pressed');
      await toggle.click();
      await expect(toggle).toHaveAttribute(
        'aria-pressed',
        wasPressed === 'true' ? 'false' : 'true',
      );
      await toggle.click();
      await expect(toggle).toHaveAttribute('aria-pressed', wasPressed!);
      await page.locator('.viewer-settings > summary').click();
      await expect(page.locator('.viewer-panel')).not.toBeVisible();
      await expect(
        page.getByRole('slider', { name: t['viewer.settings.sensitivity'] }),
      ).toBeVisible();
      await fits(page.locator('.viewer-settings-panel select'));
      if (page.viewportSize()!.width === 390) {
        for (const button of await page.locator('.viewer-axis, .viewer-segments button').all()) {
          const box = await button.boundingBox();
          expect(box!.height).toBeGreaterThanOrEqual(44);
          expect(box!.width).toBeGreaterThanOrEqual(44);
        }
      }
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(
        page.viewportSize()!.width,
      );
    });
  }
}
