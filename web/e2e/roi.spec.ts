import { expect, test } from '@playwright/test';
import type { Box3Helper, BufferGeometry, Points, ShaderMaterial } from 'three';
import en from '../src/i18n/en.json' with { type: 'json' };
import type { Roi } from '../src/viewer/roi';

// No screenshots or catalog writes: the tester owns images and backend tests
// exercise atomic persistence. Keep only ROI API state in memory while the real
// Notre Dame bundle, React route, navigation and WebGL renderer run in the browser.
test('Notre Dame exposes ROI, draws in top view, hides outside points and restores a saved box', async ({
  page,
}) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  let saved: Roi | null = null;
  let writes = 0;
  let deletions = 0;
  await page.route('**/api/sites/*/roi', async (route) => {
    switch (route.request().method()) {
      case 'GET':
        await route.fulfill(
          saved ? { status: 200, json: saved } : { status: 404, json: { detail: 'No saved ROI' } },
        );
        break;
      case 'PUT':
        saved = route.request().postDataJSON() as Roi;
        writes++;
        await route.fulfill({ status: 200, json: saved });
        break;
      case 'DELETE':
        saved = null;
        deletions++;
        await route.fulfill({ status: 204 });
        break;
      default:
        await route.fulfill({ status: 405 });
    }
  });
  await page.addInitScript(() => localStorage.setItem('qarau.lang', 'en'));
  await page.goto('/viewer/notre-dame');
  await page.waitForFunction(() => window.__qarau?.pointsLoaded, null, { timeout: 120_000 });
  const toolbar = page.getByRole('toolbar', { name: en['toolbar.tools'] });
  const tool = toolbar.getByRole('button', { name: en['roi.title'], exact: true });
  await expect(tool).toBeVisible();
  await expect(tool).toHaveText(en['roi.title']);
  await expect(tool).toHaveAttribute('aria-pressed', 'true');
  const panel = page.getByRole('region', { name: en['roi.title'] });
  await expect(panel).toBeVisible();
  await expect(page.getByRole('alert')).toHaveCount(0);
  const draw = panel.getByRole('button', { name: en['roi.draw'], exact: true });
  await expect(draw).toBeEnabled();

  await draw.click();
  const drawing = await page.evaluate(() => {
    const engine = window.__qarau!.engine!;
    const direction = engine.camera.position.clone();
    engine.camera.getWorldDirection(direction);
    return { enabled: engine.controls.enabled, direction: direction.toArray() };
  });
  expect(drawing.enabled).toBe(false);
  expect(drawing.direction[0]).toBeCloseTo(0, 6);
  expect(drawing.direction[1]).toBeCloseTo(0, 6);
  expect(drawing.direction[2]).toBeCloseTo(-1, 6);
  const canvas = page.getByLabel(en['canvas.label']);
  const viewport = (await canvas.boundingBox())!;
  // Reverse the drag. The selected middle portion must retain object points and
  // leave actual source points outside, so the toggle cannot pass as a no-op.
  await page.mouse.move(viewport.x + viewport.width * 0.75, viewport.y + viewport.height * 0.8);
  await page.mouse.down();
  await page.mouse.move(viewport.x + viewport.width * 0.25, viewport.y + viewport.height * 0.3, {
    steps: 12,
  });
  await expect(page.getByTestId('roi-drag-rectangle')).toBeVisible();
  await page.mouse.up();
  await expect(panel.getByText(en['roi.unsaved'], { exact: true })).toBeVisible();
  await expect(page.getByRole('alert')).toHaveCount(0);

  const source = await page.evaluate(() => {
    const engine = window.__qarau!.engine!;
    const helper = engine.scene.getObjectByName('roi-box') as Box3Helper;
    const cloud = engine.scene.children.find(
      (object) =>
        'geometry' in object &&
        (object as Points<BufferGeometry>).geometry.hasAttribute('firstSeen'),
    ) as Points<BufferGeometry, ShaderMaterial>;
    const positions = cloud.geometry.getAttribute('position');
    const point = engine.camera.position.clone();
    let inside = 0;
    let checksum = 0;
    for (let i = 0; i < positions.count; i++) {
      point.fromBufferAttribute(positions, i);
      if (helper.box.containsPoint(point)) inside++;
      checksum += point.x + point.y + point.z;
    }
    const material = Array.isArray(helper.material) ? helper.material[0] : helper.material;
    return {
      min: helper.box.min.toArray(),
      max: helper.box.max.toArray(),
      inside,
      outside: positions.count - inside,
      count: positions.count,
      checksum,
      visible: helper.visible,
      depthTest: material.depthTest,
      hide: cloud.material.uniforms.uHideOutside.value as number,
      enabled: engine.controls.enabled,
    };
  });
  expect(source.inside).toBeGreaterThan(0);
  expect(source.outside).toBeGreaterThan(0);
  expect(source.visible).toBe(true);
  expect(source.depthTest).toBe(false);
  expect(source.enabled).toBe(true);
  expect(source.hide).toBe(0);
  for (let axis = 0; axis < 3; axis++) expect(source.min[axis]).toBeLessThan(source.max[axis]);
  const hide = panel.getByRole('button', { name: en['roi.hideOutside'], exact: true });
  await hide.click();
  await expect(hide).toHaveAttribute('aria-pressed', 'true');
  const filtered = await page.evaluate(() => {
    const cloud = window.__qarau!.engine!.scene.children.find(
      (object) =>
        'geometry' in object &&
        (object as Points<BufferGeometry>).geometry.hasAttribute('firstSeen'),
    ) as Points<BufferGeometry, ShaderMaterial>;
    const positions = cloud.geometry.getAttribute('position');
    let checksum = 0;
    for (let i = 0; i < positions.count; i++)
      checksum += positions.getX(i) + positions.getY(i) + positions.getZ(i);
    return {
      hide: cloud.material.uniforms.uHideOutside.value as number,
      count: positions.count,
      checksum,
      discardsOutside: cloud.material.fragmentShader.includes(
        'uHideOutside > 0.5 && vRoiVisible < 0.5) discard',
      ),
    };
  });
  expect(filtered.hide).toBe(1);
  expect(filtered.discardsOutside).toBe(true);
  expect(filtered.count).toBe(source.count);
  expect(filtered.checksum).toBe(source.checksum);

  await panel.getByRole('button', { name: en['roi.save'], exact: true }).click();
  await expect(panel.getByText(en['roi.saved'], { exact: true })).toBeVisible();
  expect(writes).toBe(1);
  expect(saved).toMatchObject({
    version: 1,
    source_run: 'notre-dame',
    frame: 'reconstruction',
    up_axis: 'z',
    min: source.min,
    max: source.max,
  });
  await page.reload();
  await page.waitForFunction(() => window.__qarau?.engine?.scene.getObjectByName('roi-box'));
  await expect(panel).toBeVisible();
  const restored = await page.evaluate(() => {
    const helper = window.__qarau!.engine!.scene.getObjectByName('roi-box') as Box3Helper;
    return { min: helper.box.min.toArray(), max: helper.box.max.toArray() };
  });
  expect(restored).toEqual({ min: source.min, max: source.max });
  await panel.getByRole('button', { name: en['roi.clear'], exact: true }).click();
  await page.waitForFunction(() => !window.__qarau!.engine!.scene.getObjectByName('roi-box'));
  expect(deletions).toBe(1);
  expect(saved).toBeNull();
  await expect(page.getByRole('alert')).toHaveCount(0);
  expect(errors).toEqual([]);
});
