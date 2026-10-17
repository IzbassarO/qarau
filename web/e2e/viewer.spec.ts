/* eslint-disable @typescript-eslint/no-explicit-any -- test hook on window */
import fs from 'node:fs';
import path from 'node:path';
import { expect, test } from '@playwright/test';

const runsDir = path.resolve(process.env.QARAU_RUNS_DIR ?? '../data/runs');
const OUT = 'test-results';
// Every run with a manifest; large synthetic perf bundles only when asked for.
const runs = fs
  .readdirSync(runsDir)
  .filter((r) => fs.existsSync(path.join(runsDir, r, 'web/manifest.json')))
  .filter(
    (r) =>
      !r.startsWith('synthetic-') || r === 'synthetic-findings' || r === process.env.QARAU_PERF_RUN,
  );

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
    const focus = await page.evaluate(() => (window as any).__qarau.engine.focus);
    console.log(`FOCUS ${run}: ${focus.method} r=${focus.radius.toFixed(2)}`);

    // scrub to the middle
    const slider = page.getByRole('slider', { name: /Позиция|Position/ });
    const max = Number(await slider.getAttribute('max'));
    await slider.fill(String(Math.floor(max / 2)));
    await page.waitForTimeout(800);
    await page.screenshot({ path: `${OUT}/${run}-2-scrub.png` });

    // the last frame is often unregistered (no pose); the panel must still render
    await slider.fill(String(max));
    await page.waitForTimeout(300);
    await page.screenshot({ path: `${OUT}/${run}-2b-last.png` });
    await slider.fill(String(Math.floor(max / 2)));
    await page.waitForTimeout(300);

    // through-camera on a rolled frame (synthetic-findings rolls every 8th frame by 30 deg)
    if (run === 'synthetic-findings') {
      await slider.fill('59');
      await page.getByRole('button', { name: 'Смотреть глазами камеры' }).click();
      await page.waitForTimeout(1200);
      await page.screenshot({ path: `${OUT}/${run}-3a-camera-rolled.png` });
      await slider.fill(String(Math.floor(max / 2)));
    }

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

    // findings (v2 bundles): list, selection, ticks, timecodes, jump
    const items = page.getByTestId('finding-item');
    if ((await items.count()) > 0) {
      await slider.fill('100');
      await page.waitForTimeout(500);
      await page.screenshot({ path: `${OUT}/${run}-5-findings.png` });
      await items.first().click();
      await expect(page.getByTestId('finding-detail')).toBeVisible();
      const ticks = page.getByTestId('finding-tick');
      expect(await ticks.count()).toBeGreaterThan(0);
      await expect(ticks.first()).toBeVisible();
      const codes = page.getByTestId('finding-timecode');
      expect(await codes.count()).toBeGreaterThan(0);
      await codes.nth(Math.min(5, (await codes.count()) - 1)).click();
      await page.waitForTimeout(1200);
      await page.screenshot({ path: `${OUT}/${run}-6-finding-jump.png` });
      // a low-evidence finding is marked, not hidden
      if (run === 'synthetic-findings') {
        await items.nth(2).click();
        await expect(page.getByText(/мало данных|low evidence/i).first()).toBeVisible();
        await page.screenshot({ path: `${OUT}/${run}-7-low-evidence.png` });
      }
    }

    // perf: orbit continuously and read the rAF-based fps
    await page.evaluate(() => (window as any).__qarau.bench(true));
    await page.waitForTimeout(4000);
    const fps = await page.evaluate(() => (window as any).__qarau.fps);
    fs.writeFileSync(`${OUT}/${run}-perf.json`, JSON.stringify({ run, loadMs, fps }));
    console.log(`PERF ${run}: load ${loadMs} ms, ${fps.toFixed(1)} fps`);

    expect(errors).toEqual([]);
  });
}

test('missing or broken findings file says so plainly', async ({ page }) => {
  test.skip(!runs.includes('synthetic-findings'), 'needs the synthetic-findings run');
  for (const [status, body] of [
    [404, 'nope'],
    [200, '{not json'],
  ] as const) {
    await page.route('**/files/findings/findings.json', (r) => r.fulfill({ status, body }));
    await page.goto('/viewer/synthetic-findings');
    await expect(page.getByRole('alert')).toBeVisible({ timeout: 60_000 });
    await page.screenshot({ path: `${OUT}/findings-error-${status}.png` });
    await page.unroute('**/files/findings/findings.json');
  }
});

test('missing run says so plainly', async ({ page }) => {
  await page.goto('/viewer/does-not-exist');
  await expect(page.getByRole('alert')).toBeVisible();
});

test('signed-out home asks for login', async ({ page }) => {
  await page.goto('/');
  await expect(page.locator('input[name="username"]')).toBeVisible();
  await expect(page.locator('input[type="password"]')).toBeVisible();
  await expect(page.getByRole('navigation')).toHaveCount(0);
});
