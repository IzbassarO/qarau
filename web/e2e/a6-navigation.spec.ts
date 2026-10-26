import { openTools } from './chrome';
import { expect, test } from '@playwright/test';
import en from '../src/i18n/en.json' with { type: 'json' };

// No image capture: the tester owns screenshots. These checks exercise real WebGL/routes.
for (const run of ['notre-dame', 'room', 'aegviidu']) {
  test(`A6 controls and framed point cloud: ${run}`, async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', (error) => errors.push(error.message));
    page.on('console', (message) => {
      if (message.type() === 'error') errors.push(message.text());
    });
    await page.addInitScript(() => localStorage.setItem('qarau.lang', 'en'));
    await page.goto(`/viewer/${run}`);
    await page.waitForFunction(() => window.__qarau?.pointsLoaded, null, { timeout: 120_000 });
    await expect(page.getByRole('alert')).toHaveCount(0);
    await expect(page.getByText(en['error.webgl'])).toHaveCount(0);
    const canvas = page.getByLabel(en['canvas.label']);
    await expect(canvas).toBeVisible();
    const modes = page.getByRole('group', { name: en['viewer.mode.label'] });
    await expect(
      modes.getByRole('button', { name: en['viewer.mode.orbit'], exact: true }),
    ).toHaveAttribute('aria-pressed', 'true');
    await expect(
      modes.getByRole('button', { name: en['viewer.mode.walk'], exact: true }),
    ).toBeDisabled();
    // Observer is a real mode since A9 (#37): available, not active until chosen
    const observer = modes.getByRole('button', { name: en['viewer.mode.observer'], exact: true });
    await expect(observer).toBeEnabled();
    await expect(observer).toHaveAttribute('aria-pressed', 'false');
    await openTools(page);
    for (const label of [
      'toolbar.top',
      'toolbar.front',
      'toolbar.side',
      'toolbar.threeQuarter',
      'toolbar.reset',
      'toolbar.alignSurface',
    ] as const)
      await expect(page.getByRole('button', { name: en[label], exact: true })).toBeVisible();
    await page.getByText(en['viewer.settings.title'], { exact: true }).click();
    const sensitivity = page.getByRole('slider', { name: en['viewer.settings.sensitivity'] });
    await expect(sensitivity).toBeVisible();
    await sensitivity.fill('1.5');
    expect(await page.evaluate(() => window.__qarau!.engine!.controls.sensitivity)).toBe(1.5);
    await page.getByText(en['viewer.settings.title'], { exact: true }).click();

    const framing = await page.evaluate(() => {
      const engine = window.__qarau!.engine!;
      const camera = engine.camera;
      const points = engine.scene.children.find(
        (object) =>
          'isPoints' in object && object.name !== 'far' && object.name !== 'camera-path-dots',
      );
      if (!points || !('geometry' in points)) throw new Error('Point cloud missing');
      const positions = (points as import('three').Points).geometry.getAttribute('position');
      let visible = 0;
      let sampled = 0;
      const p = camera.position.clone();
      for (let i = 0; i < positions.count; i += Math.max(1, Math.floor(positions.count / 2000))) {
        p.fromBufferAttribute(positions, i).project(camera);
        sampled++;
        if (Math.abs(p.x) < 1 && Math.abs(p.y) < 1 && Math.abs(p.z) < 1) visible++;
      }
      const right = camera.position.clone().set(1, 0, 0).applyQuaternion(camera.quaternion);
      const dots = engine.scene.getObjectByName('camera-path-dots') as import('three').Points<
        import('three').BufferGeometry,
        import('three').PointsMaterial
      >;
      return {
        visibleFraction: visible / sampled,
        points: positions.count,
        mapGround: engine.renderer.getClearColor(dots.material.color.clone()).getHex() === 0x131313,
        up: camera.up.toArray(),
        horizonTilt: Math.abs(right.z),
        frustums: engine.scene.children.filter((object) => object.type === 'LineSegments').length,
      };
    });
    expect(framing.points).toBeGreaterThan(100);
    expect(framing.visibleFraction).toBeGreaterThan(0.8);
    expect(framing.mapGround).toBe(true);
    expect(framing.up).toEqual([0, 0, 1]);
    expect(framing.horizonTilt).toBeLessThan(1e-7);
    expect(framing.frustums).toBe(1);
    await canvas.focus();
    await page.keyboard.press('2');
    await page.keyboard.press('3');
    await expect(
      modes.getByRole('button', { name: en['viewer.mode.orbit'], exact: true }),
    ).toHaveAttribute('aria-pressed', 'true');
    await openTools(page);
    await page.getByRole('button', { name: en['toolbar.alignSurface'], exact: true }).click();
    await expect(page.getByRole('status')).toHaveText(en['canvas.alignHint']);
    await page.keyboard.press('Escape');
    await expect(page.getByRole('status')).toHaveText(en['canvas.hint']);
    expect(errors).toEqual([]);
  });
}
