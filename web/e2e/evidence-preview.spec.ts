import { expect, test } from '@playwright/test';
import { build, preview } from 'vite';

// Exercise compiled app code and the real local run service. No page.route API mocks.
for (const theme of ['light', 'dark'] as const) {
  test(`compiled preview opens /viewer/notre-dame-p3e?finding=first: ${theme}`, async ({
    page,
  }, testInfo) => {
    const outDir = testInfo.outputPath('preview-dist');
    await build({ logLevel: 'silent', build: { outDir } });
    const server = await preview({
      logLevel: 'silent',
      build: { outDir },
      preview: { port: 0, strictPort: true, host: '127.0.0.1', open: false },
    });
    try {
      const address = server.httpServer.address();
      if (!address || typeof address === 'string')
        throw new Error('Preview did not bind a TCP port');
      await page.emulateMedia({ colorScheme: theme });
      await page.addInitScript(() => {
        localStorage.setItem('qarau.lang', 'en');
        sessionStorage.setItem('qarau.auth.session', 'Bearer test-api-value');
      });
      // Simulate a screenshot runner that does not forward the viewer path in Referer.
      await page.setExtraHTTPHeaders({ Referer: 'http://127.0.0.1/' });
      await page.goto(`http://127.0.0.1:${address.port}/viewer/notre-dame-p3e?finding=first`);
      const panel = page.getByRole('complementary', { name: 'Evidence' });
      await expect(panel.getByRole('heading', { name: 'Corrosion' })).toBeVisible();
      await expect(panel.locator('.evidence-thumbnails img')).toHaveCount(3);
      await expect(panel.locator('.evidence-thumbnails polygon')).toHaveCount(3);
      await expect(panel.locator('.qv-sev.critical')).toHaveText('Critical');
      await expect(panel.locator('.evidence-main-frame img')).toBeVisible();
      await expect(panel.locator('.evidence-main-frame polygon')).toHaveCount(1);
      await expect(panel.getByText('1.2 m²')).toBeVisible();
      await expect(page.getByRole('alert')).toHaveCount(0);
      await expect(panel.getByText('No findings in this run.')).toHaveCount(0);
      for (const image of await panel.locator('img').all()) {
        await expect(image).toHaveJSProperty('complete', true);
        await expect
          .poll(() => image.evaluate((element: HTMLImageElement) => element.naturalWidth))
          .toBeGreaterThan(0);
      }
      await expect(page.getByRole('alert')).toHaveCount(0);
    } finally {
      await server.close();
    }
  });
}
