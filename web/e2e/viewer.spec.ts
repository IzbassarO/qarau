/* eslint-disable @typescript-eslint/no-explicit-any -- test hook on window */
import fs from 'node:fs';
import path from 'node:path';
import { expect, test } from '@playwright/test';

const runsDir = path.resolve(process.env.QARAU_RUNS_DIR ?? '../../../../data/runs');
const OUT = 'test-results';
const runs = [
  'synthetic',
  'south-building',
  'notre-dame',
  ...(process.env.QARAU_PERF_RUN ? [process.env.QARAU_PERF_RUN] : []),
].filter((r) => fs.existsSync(path.join(runsDir, r, 'web/manifest.json')));

for (const run of runs) {
  test(`viewer: ${run}`, async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', (e) => errors.push(String(e)));
    page.on('console', (m) => m.type() === 'error' && errors.push(m.text()));

    const t0 = Date.now();
    await page.goto(`/viewer/${run}`);
    await page.waitForFunction(() => (window as any).__qarau?.pointsLoaded, null, {
      timeout: 120_000,
    });
    const loadMs = Date.now() - t0;
    await page.waitForTimeout(1500);
    await page.screenshot({ path: `${OUT}/${run}-1-initial.png` });

    // scrub to the middle
    const slider = page.getByRole('slider', { name: /Позиция|Position/ });
    const max = Number(await slider.getAttribute('max'));
    await slider.fill(String(Math.floor(max / 2)));
    await page.waitForTimeout(800);
    await page.screenshot({ path: `${OUT}/${run}-2-scrub.png` });

    // view through the current camera
    await page.getByRole('button', { name: 'Смотреть глазами камеры' }).click();
    await page.waitForTimeout(1200);
    await page.screenshot({ path: `${OUT}/${run}-3-camera.png` });

    // click a point at the canvas centre region: try a few positions until one is picked
    const box = (await page.locator('canvas').boundingBox())!;
    await page.getByRole('button', { name: 'Сбросить вид' }).click();
    await page.waitForTimeout(800);
    for (const [fx, fy] of [
      [0.5, 0.5],
      [0.45, 0.5],
      [0.55, 0.45],
      [0.5, 0.6],
      [0.4, 0.4],
    ]) {
      await page.mouse.click(box.x + box.width * fx, box.y + box.height * fy);
      if ((await page.getByText(/Видна в этих моментах/).count()) > 0) break;
    }
    await page.waitForTimeout(500);
    await page.screenshot({ path: `${OUT}/${run}-4-picked.png` });

    // perf: orbit continuously and read the rAF-based fps
    await page.evaluate(() => (window as any).__qarau.bench(true));
    await page.waitForTimeout(4000);
    const fps = await page.evaluate(() => (window as any).__qarau.fps);
    fs.writeFileSync(`${OUT}/${run}-perf.json`, JSON.stringify({ run, loadMs, fps }));
    console.log(`PERF ${run}: load ${loadMs} ms, ${fps.toFixed(1)} fps`);

    expect(errors).toEqual([]);
  });
}

test('missing run says so plainly', async ({ page }) => {
  await page.goto('/viewer/does-not-exist');
  await expect(page.getByRole('alert')).toBeVisible();
});

test('runs list links to viewer', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('link').nth(0)).toBeVisible();
  await page.screenshot({ path: `${OUT}/runs.png` });
});
