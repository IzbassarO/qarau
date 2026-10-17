import { expect, test } from '@playwright/test';
import en from '../src/i18n/en.json' with { type: 'json' };

// Real WebGL and exported Notre-Dame evidence. Screenshots belong to the tester.
test('Notre-Dame deep link opens point evidence and thumbnails seek the source frame', async ({
  page,
}) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.addInitScript(() => localStorage.setItem('qarau.lang', 'en'));
  await page.goto('/viewer/notre-dame?point=0');
  await page.waitForFunction(() => window.__qarau?.pointsLoaded, null, { timeout: 120_000 });
  await expect(page.getByRole('alert')).toHaveCount(0);
  await expect(page.getByText(en['error.webgl'])).toHaveCount(0);
  const panel = page.getByRole('region', { name: en['pointInfo.title'] });
  await expect(panel).toBeVisible();
  await expect(panel.getByText(en['pointInfo.coordinates'], { exact: false })).toBeVisible();
  await expect(panel.getByText(en['pointInfo.views'], { exact: true })).toBeVisible();
  await expect(panel.getByText(en['pointInfo.needsScale'])).toHaveCount(2);
  await expect(panel.getByText(en['pointInfo.needsConfidence'])).toBeVisible();
  const evidence = panel.getByRole('img');
  if (await evidence.count()) {
    await expect(evidence.first()).toBeVisible();
    await expect
      .poll(() => evidence.first().evaluate((image) => (image as HTMLImageElement).naturalWidth))
      .toBeGreaterThan(0);
    const thumbnail = panel.getByRole('button', { name: /Jump to frame/ }).last();
    if (await thumbnail.isEnabled()) {
      const id = Number((await thumbnail.getAttribute('aria-label'))!.split(' ').at(-1));
      await thumbnail.click();
      await expect(page.getByRole('slider', { name: en['timeline.position'] })).toHaveValue(
        String(id),
      );
    }
  } else {
    await expect(panel.getByText(en['pointInfo.noFrames'])).toBeVisible();
  }
  const state = await page.evaluate(() => {
    const engine = window.__qarau!.engine!;
    const marker = engine.scene.getObjectByName('selected-point')!;
    const color = (
      marker as import('three').Points<
        import('three').BufferGeometry,
        import('three').PointsMaterial
      >
    ).material.color.clone();
    return { black: engine.renderer.getClearColor(color).getHex() === 0, selected: marker.visible };
  });
  expect(state).toEqual({ black: true, selected: true });
  expect(errors).toEqual([]);
});

test('Notre-Dame info tool picks on a click while drag still orbits', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.addInitScript(() => localStorage.setItem('qarau.lang', 'en'));
  await page.goto('/viewer/notre-dame');
  await page.waitForFunction(() => window.__qarau?.pointsLoaded, null, { timeout: 120_000 });
  await expect(page.getByRole('alert')).toHaveCount(0);
  const tool = page.getByRole('button', { name: en['pointInfo.tool'] });
  await expect(tool).toBeVisible();
  await expect(tool).toHaveAttribute('aria-pressed', 'false');
  await tool.click();
  await expect(tool).toHaveAttribute('aria-pressed', 'true');
  const canvas = page.getByLabel(en['canvas.label']);
  const box = (await canvas.boundingBox())!;
  const start = { x: box.x + box.width / 2, y: box.y + box.height / 2 };
  await page.mouse.move(start.x, start.y);
  await page.mouse.down();
  await page.mouse.move(start.x + 50, start.y + 20, { steps: 5 });
  await page.mouse.move(start.x, start.y, { steps: 5 });
  await page.mouse.up();
  await expect(page.getByRole('region', { name: en['pointInfo.title'] })).toHaveCount(0);
  // Wait for navigation damping, then locate a real projected cloud point away from overlays.
  await expect.poll(() => page.evaluate(() => window.__qarau!.engine!.controls.enabled)).toBe(true);
  const pixel = await page.evaluate(() => {
    const engine = window.__qarau!.engine!;
    const cloud = engine.scene.children.find(
      (object) => 'isPoints' in object && object.name === '',
    ) as import('three').Points;
    const positions = cloud.geometry.getAttribute('position');
    for (let i = 0; i < positions.count; i += 7) {
      const p = engine.project([positions.getX(i), positions.getY(i), positions.getZ(i)]);
      if (p.visible && p.x > 100 && p.y > 100 && p.x < 500 && p.y < 500) return p;
    }
    throw new Error('No visible cloud point found');
  });
  await page.mouse.click(box.x + pixel.x, box.y + pixel.y);
  const panel = page.getByRole('region', { name: en['pointInfo.title'] });
  await expect(panel).toBeVisible();
  await panel.getByRole('button', { name: en['pointInfo.close'] }).click();
  await expect(panel).toHaveCount(0);
  await expect(tool).toHaveAttribute('aria-pressed', 'false');
  expect(errors).toEqual([]);
});
