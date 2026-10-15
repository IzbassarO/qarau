import { expect, test } from '@playwright/test';
import en from '../src/i18n/en.json' with { type: 'json' };

test('demo axis views and fly keyboard/mouse controls', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.addInitScript(() => {
    localStorage.setItem('qarau.lang', 'en');
    // Exercise the drag fallback with genuine browser pointer events.
    Object.defineProperty(HTMLCanvasElement.prototype, 'requestPointerLock', { value: undefined });
  });
  await page.goto('/viewer/demo');
  await page.waitForFunction(() => window.__qarau?.pointsLoaded);
  await expect(page.getByRole('alert')).toHaveCount(0);
  await expect(page.getByText(en['error.webgl'])).toHaveCount(0);
  const canvas = page.getByLabel(en['canvas.label']);
  const gizmo = page.getByRole('group', { name: en['gizmo.label'] });
  await expect(gizmo.getByRole('button')).toHaveCount(6);
  // Diagonal views used to project two axis pairs onto the same screen points.
  // Check actual browser hit targets, without screenshots or forced clicks.
  await page.evaluate(() => {
    const engine = window.__qarau!.engine!;
    const pivot = engine.controls.target;
    const distance = engine.camera.position.distanceTo(pivot);
    const offset = engine.camera.position.clone().set(1, 1, 0).normalize();
    engine.camera.position.copy(pivot).addScaledVector(offset, distance);
    engine.controls.sync();
    engine.setFrame(0);
  });
  await expect
    .poll(() =>
      gizmo.getByRole('button').evaluateAll((buttons) =>
        buttons
          .filter((button) => {
            const rect = button.getBoundingClientRect();
            return (
              document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2) !==
              button
            );
          })
          .map((button) => button.getAttribute('aria-label')),
      ),
    )
    .toEqual([]);
  const pivot = await page.evaluate(() => window.__qarau!.engine!.controls.target.toArray());
  const distance = await page.evaluate(() => {
    const engine = window.__qarau!.engine!;
    return engine.camera.position.distanceTo(engine.controls.target);
  });
  for (const [label, direction] of [
    [en['gizmo.positiveZ'], [0, 0, -1]],
    [en['gizmo.negativeZ'], [0, 0, 1]],
    [en['gizmo.positiveX'], [-1, 0, 0]],
    [en['gizmo.negativeX'], [1, 0, 0]],
    [en['gizmo.positiveY'], [0, -1, 0]],
    [en['gizmo.negativeY'], [0, 1, 0]],
  ] as const) {
    await gizmo.getByRole('button', { name: label, exact: true }).click();
    await page.waitForFunction((expected) => {
      const camera = window.__qarau!.engine!.camera;
      const forward = camera.getWorldDirection(camera.position.clone());
      return forward.toArray().every((value, axis) => Math.abs(value - expected[axis]) < 0.001);
    }, direction);
    const view = await page.evaluate(() => {
      const engine = window.__qarau!.engine!;
      return {
        distance: engine.camera.position.distanceTo(engine.controls.target),
        pivot: engine.controls.target.toArray(),
      };
    });
    expect(view.distance).toBeCloseTo(distance, 5);
    expect(view.pivot).toEqual(pivot);
  }

  await canvas.focus();
  await page.keyboard.press('f');
  await expect(page.getByRole('status')).toHaveText(en['canvas.flyHint']);
  const start = await page.evaluate(() => window.__qarau!.engine!.camera.position.toArray());
  await page.keyboard.down('w');
  await page.waitForFunction((position) => {
    const current = window.__qarau!.engine!.camera.position.toArray();
    return Math.hypot(...current.map((value, axis) => value - position[axis])) > 0.5;
  }, start);
  await page.keyboard.up('w');

  const rotation = await page.evaluate(() => window.__qarau!.engine!.camera.quaternion.toArray());
  const box = (await canvas.boundingBox())!;
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width / 2 + 80, box.y + box.height / 2 + 20, { steps: 8 });
  await page.mouse.up();
  await page.waitForFunction((before) => {
    const after = window.__qarau!.engine!.camera.quaternion.toArray();
    return Math.abs(after.reduce((dot, value, index) => dot + value * before[index], 0)) < 0.999;
  }, rotation);
  await page.keyboard.press('Escape');
  await expect(page.getByRole('status')).toHaveText(en['canvas.hint']);
  expect(await page.evaluate(() => window.__qarau!.engine!.flyMode)).toBe(false);
  expect(errors).toEqual([]);
});
