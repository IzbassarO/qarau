import { expect, test, type Page } from '@playwright/test';
import { readDenseFixture } from '../src/fixtures/dense-bundle/readFixture';
import en from '../src/i18n/en.json' with { type: 'json' };
import { runUrl } from '../src/lib/bundle';

async function serveDenseFixture(page: Page) {
  const { manifest, files } = readDenseFixture();
  await page.addInitScript(() => localStorage.setItem('qarau.lang', 'en'));
  await page.route(`**/api/runs/${manifest.run_id}/manifest.json`, (route) =>
    route.fulfill({ json: manifest }),
  );
  for (const [relative, body] of files)
    await page.route(`**${runUrl(manifest.run_id, relative)}`, (route) =>
      route.fulfill({
        body,
        contentType: relative.endsWith('.svg') ? 'image/svg+xml' : 'application/octet-stream',
      }),
    );
  await page.goto(`/viewer/${manifest.run_id}`);
  await page.waitForFunction(() => window.__qarau?.pointsLoaded);
}

test('dense fixture toggles independently and a picked dense point lists its source frames', async ({
  page,
}) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await serveDenseFixture(page);
  await expect(page.getByRole('alert')).toHaveCount(0);
  await expect(page.getByText(en['error.webgl'])).toHaveCount(0);
  const toggle = page.getByRole('button', { name: en['viewer.show.densePoints'] });
  await expect(toggle).toHaveAttribute('aria-pressed', 'true');
  await expect
    .poll(() => page.evaluate(() => window.__qarau!.denseLayer()))
    .toEqual({
      available: true,
      visible: true,
      count: 4,
    });

  await toggle.click();
  await expect(toggle).toHaveAttribute('aria-pressed', 'false');
  await expect
    .poll(() => page.evaluate(() => window.__qarau!.denseLayer()))
    .toEqual({
      available: true,
      visible: false,
      count: 4,
    });
  await toggle.click();
  await expect(toggle).toHaveAttribute('aria-pressed', 'true');
  await expect
    .poll(() => page.evaluate(() => window.__qarau!.denseLayer()))
    .toEqual({
      available: true,
      visible: true,
      count: 4,
    });

  const tool = page.getByRole('button', { name: en['pointInfo.tool'] });
  await tool.click();
  await expect(tool).toHaveAttribute('aria-pressed', 'true');
  const canvas = page.getByLabel(en['canvas.label']);
  const box = (await canvas.boundingBox())!;
  const point = await page.evaluate(() => window.__qarau!.densePointScreen(0));
  expect(point).not.toBeNull();
  await page.mouse.click(box.x + point!.x, box.y + point!.y);

  const panel = page.getByRole('region', { name: en['pointInfo.title'] });
  await expect(panel).toBeVisible();
  // CSR [0, 2] indexes manifest.frames; these entries have non-contiguous IDs 10 and 30.
  await expect(
    panel.getByRole('button', { name: `${en['pointInfo.seekFrame']} 10`, exact: true }),
  ).toBeVisible();
  await expect(
    panel.getByRole('button', { name: `${en['pointInfo.seekFrame']} 30`, exact: true }),
  ).toBeVisible();
  await expect(
    panel.getByRole('button', { name: `${en['pointInfo.seekFrame']} 20`, exact: true }),
  ).toHaveCount(0);
  await expect(panel.getByRole('img')).toHaveCount(2);
  await expect(panel.getByText(en['pointInfo.colmapFusionNote'], { exact: true })).toBeVisible();
  await expect(panel.getByText(en['pointInfo.estimated'], { exact: true })).toHaveCount(0);
  expect(errors).toEqual([]);
});
