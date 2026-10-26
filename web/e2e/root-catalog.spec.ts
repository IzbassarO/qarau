import { expect, test } from '@playwright/test';
import en from '../src/i18n/en.json' with { type: 'json' };
import ru from '../src/i18n/ru.json' with { type: 'json' };
import kk from '../src/i18n/kk.json' with { type: 'json' };
import catalog from '../src/test/fixtures/legacy-export-catalog.json' with { type: 'json' };

for (const theme of ['light', 'dark'] as const) {
  for (const [lang, t] of Object.entries({ en, ru, kk })) {
    test(`root / lists legacy API inspection rows with an empty inspection database: ${lang}, ${theme}`, async ({
      page,
    }) => {
      await page.emulateMedia({ colorScheme: theme });
      await page.addInitScript((locale) => {
        localStorage.setItem('qarau.lang', locale);
        sessionStorage.setItem('qarau.auth.session', 'Bearer test-api-value');
      }, lang);
      const requests: string[] = [];
      await page.route('**/api/**', (route) => {
        const request = route.request();
        const pathname = new URL(request.url()).pathname;
        requests.push(pathname);
        expect(request.method()).toBe('GET');
        expect(request.headers().authorization).toBe('Bearer test-api-value');
        // These are the real read-only catalog fields: no invented title or status.
        if (pathname === '/api/inspections') return route.fulfill({ json: [] });
        if (pathname === '/api/runs') return route.fulfill({ json: catalog });
        throw new Error(`Unexpected root request: ${pathname}`);
      });

      await page.goto('/');
      const navigation = page.getByRole('navigation', { name: t['shell.sites'] });
      await expect(navigation.locator('.inspection-link')).toHaveCount(2);
      await expect(navigation.locator('.inspection-link-name')).toHaveText([
        'walkdown-2026-10-23',
        'tank-survey-2026-10-22',
      ]);
      await expect(navigation.getByRole('link', { name: /walkdown-2026-10-23/ })).toHaveAttribute(
        'href',
        '/inspections/walkdown-2026-10-23',
      );
      await expect(
        navigation.getByRole('link', { name: /tank-survey-2026-10-22/ }),
      ).toHaveAttribute('href', '/inspections/tank-survey-2026-10-22');
      await expect(navigation.locator('.inspection-status-chip.unknown')).toHaveCount(2);
      await expect(navigation.getByText(t['status.unknown'], { exact: true })).toHaveCount(2);
      await page.evaluate(() => document.fonts.ready);
      for (const row of await navigation.locator('.inspection-link').all()) {
        const name = row.locator('.inspection-link-name');
        const status = row.locator('.inspection-status-chip');
        await expect(status).toHaveCSS('white-space', 'nowrap');
        await expect(status).toHaveCSS('flex-shrink', '0');
        await expect(name).toHaveCSS('text-overflow', 'ellipsis');
        const rowBox = await row.boundingBox();
        expect(rowBox).not.toBeNull();
        expect(rowBox!.height).toBeLessThanOrEqual(40);
        const statusSize = await status.evaluate((element) => ({
          content: element.scrollWidth,
          visible: element.clientWidth,
        }));
        expect(statusSize.content).toBeLessThanOrEqual(statusSize.visible);
        if (lang === 'en' || lang === 'ru') {
          const nameBox = await name.boundingBox();
          const dateBox = await row.getByRole('time').boundingBox();
          const statusBox = await status.boundingBox();
          expect(nameBox).not.toBeNull();
          expect(dateBox).not.toBeNull();
          expect(statusBox).not.toBeNull();
          expect(nameBox!.width).toBeGreaterThanOrEqual(190);
          expect(dateBox!.y).toBeGreaterThanOrEqual(nameBox!.y + nameBox!.height);
          expect(Math.abs(dateBox!.y - statusBox!.y)).toBeLessThanOrEqual(1);
          expect(dateBox!.x + dateBox!.width).toBeLessThanOrEqual(statusBox!.x);
          expect(statusBox!.x + statusBox!.width).toBeLessThanOrEqual(rowBox!.x + rowBox!.width);
        }
      }
      await expect(navigation.getByText(t['status.done'], { exact: true })).toHaveCount(0);
      await expect(navigation.getByText(t['shell.noSites'], { exact: true })).toHaveCount(0);
      await expect(navigation.getByRole('button', { name: t['inspection.new'] })).toBeVisible();
      await expect(page.getByRole('alert')).toHaveCount(0);
      expect(requests).toContain('/api/inspections');
      expect(requests).toContain('/api/runs');
    });
  }
}
