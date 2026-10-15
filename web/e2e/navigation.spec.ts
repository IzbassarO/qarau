import { expect, test, type Page } from '@playwright/test';
import en from '../src/i18n/en.json';

async function snapshot(page: Page) {
  return page.evaluate(() => {
    const engine = window.__qarau!.engine!;
    return {
      position: engine.camera.position.toArray(),
      pivot: engine.controls.target.toArray(),
      rotation: engine.camera.quaternion.toArray(),
      distance: engine.camera.position.distanceTo(engine.controls.target),
    };
  });
}

async function rest(page: Page) {
  await page.evaluate(
    () =>
      new Promise<void>((resolve, reject) => {
        const camera = window.__qarau!.engine!.camera;
        const previous = camera.position.clone();
        let stable = 0;
        let frames = 0;
        const tick = () => {
          stable = previous.distanceTo(camera.position) < 1e-6 ? stable + 1 : 0;
          previous.copy(camera.position);
          if (stable >= 8) resolve();
          else if (++frames >= 300) reject(new Error('Navigation did not settle'));
          else requestAnimationFrame(tick);
        };
        requestAnimationFrame(tick);
      }),
  );
}

const distance = (a: number[], b: number[]) => Math.hypot(...a.map((v, i) => v - b[i]));

test('demo navigation: orbit, pan, trackpad, close zoom and bounds', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  page.on('console', (message) => {
    if (message.type() === 'error') errors.push(message.text());
  });
  await page.addInitScript(() => localStorage.setItem('qarau.lang', 'en'));
  await page.goto('/viewer/demo');
  await page.waitForFunction(() => window.__qarau?.pointsLoaded);
  await page.evaluate(() => document.fonts.ready);
  const canvas = page.locator('canvas');
  const box = (await canvas.boundingBox())!;
  const x = box.x + box.width * 0.5;
  const y = box.y + box.height * 0.5;
  await expect(page.getByText(en['error.webgl'], { exact: true })).toHaveCount(0);
  await expect(page.locator('vite-error-overlay')).toHaveCount(0);
  await page.screenshot({ path: 'test-results/a2a-controls/demo-navigation.png', fullPage: true });

  await page.mouse.move(x, y);
  await page.mouse.down();
  const picked = await snapshot(page);
  await page.mouse.move(x + 65, y + 20, { steps: 10 });
  await page.mouse.up();
  await rest(page);
  const orbited = await snapshot(page);
  expect(distance(orbited.position, picked.position)).toBeGreaterThan(0.1);
  expect(distance(orbited.pivot, picked.pivot)).toBeLessThan(1e-6);
  expect(orbited.distance).toBeCloseTo(picked.distance, 4);

  for (const button of ['left', 'middle'] as const) {
    const before = await snapshot(page);
    await page.mouse.move(x, y);
    if (button === 'left') await page.keyboard.down('Shift');
    await page.mouse.down({ button });
    await page.mouse.move(x + 30, y + 15, { steps: 8 });
    await page.mouse.up({ button });
    if (button === 'left') await page.keyboard.up('Shift');
    await rest(page);
    const after = await snapshot(page);
    expect(distance(after.position, before.position)).toBeGreaterThan(0.05);
    expect(after.distance).toBeCloseTo(before.distance, 4);
  }

  const beforeTrackpad = await snapshot(page);
  await canvas.dispatchEvent('wheel', { deltaX: 12.5, deltaY: 3.5, clientX: x, clientY: y });
  await rest(page);
  const afterTrackpad = await snapshot(page);
  expect(distance(afterTrackpad.rotation, beforeTrackpad.rotation)).toBeGreaterThan(0.001);
  expect(distance(afterTrackpad.pivot, beforeTrackpad.pivot)).toBeLessThan(1e-6);
  await canvas.dispatchEvent('wheel', { shiftKey: true, deltaX: 8, deltaY: 5 });
  await rest(page);
  expect(distance((await snapshot(page)).pivot, afterTrackpad.pivot)).toBeGreaterThan(0.01);

  for (let i = 0; i < 35; i++) {
    await canvas.dispatchEvent('wheel', {
      ctrlKey: true,
      deltaY: -120,
      clientX: x,
      clientY: y,
    });
    await page.evaluate(
      () => new Promise<void>((resolve) => requestAnimationFrame(() => resolve())),
    );
  }
  await rest(page);
  const close = await snapshot(page);
  expect(close.distance).toBeGreaterThan(0);
  expect(close.position.every(Number.isFinite)).toBe(true);
  // Repeated extreme zoom-out must reach a finite limit rather than escape the map.
  for (let i = 0; i < 8; i++) {
    await canvas.dispatchEvent('wheel', { deltaMode: 1, deltaY: 1000, clientX: x, clientY: y });
    await rest(page);
  }
  const boundary = await snapshot(page);
  await canvas.dispatchEvent('wheel', { deltaMode: 1, deltaY: 1000, clientX: x, clientY: y });
  await rest(page);
  expect(distance((await snapshot(page)).position, boundary.position)).toBeLessThan(1e-5);

  await page.getByRole('button', { name: en['toolbar.reset'], exact: true }).click();
  await rest(page);
  expect(
    await page.evaluate(() => {
      const gl = window.__qarau!.engine!.renderer.getContext();
      return Array.from(gl.getParameter(gl.COLOR_CLEAR_VALUE) as Float32Array);
    }),
  ).toEqual([0, 0, 0, 1]);
  await expect(page.getByText(en['error.webgl'], { exact: true })).toHaveCount(0);
  await expect(page.locator('vite-error-overlay')).toHaveCount(0);
  await page.screenshot({
    path: 'test-results/a2a-controls/demo-after-navigation.png',
    fullPage: true,
  });
  expect(errors).toEqual([]);
});
