import { expect, test } from '@playwright/test';
import type { Box3Helper, BufferGeometry, Points, ShaderMaterial } from 'three';
import en from '../src/i18n/en.json' with { type: 'json' };
import type { Manifest } from '../src/lib/bundle';
import { boxFromPointsInRect, type Roi } from '../src/viewer/roi';

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

test('a fresh Notre Dame route displays a previously saved fixture outline without drawing first', async ({
  page,
}) => {
  // Derive a fixture from the actual object buffer. This is an explicit mocked
  // saved document, never an automatic default in product code or a real write.
  const manifestResponse = await page.request.get('/api/runs/notre-dame/manifest.json');
  expect(manifestResponse.ok()).toBe(true);
  const manifest = (await manifestResponse.json()) as Manifest;
  const positionResponse = await page.request.get(
    `/api/runs/notre-dame/files/${manifest.points.position}`,
  );
  expect(positionResponse.ok()).toBe(true);
  const bytes = await positionResponse.body();
  expect(bytes.length).toBe(manifest.points.count * 12);
  const positions = new Float32Array(bytes.length / 4);
  for (let i = 0; i < positions.length; i++) positions[i] = bytes.readFloatLE(i * 4);
  const xs: number[] = [];
  const ys: number[] = [];
  for (let i = 0; i < positions.length; i += 3)
    if ([positions[i], positions[i + 1], positions[i + 2]].every(Number.isFinite)) {
      xs.push(positions[i]);
      ys.push(positions[i + 1]);
    }
  xs.sort((a, b) => a - b);
  ys.sort((a, b) => a - b);
  const lo = Math.floor((xs.length - 1) * 0.02);
  const hi = Math.floor((xs.length - 1) * 0.98);
  const box = boxFromPointsInRect(
    positions,
    { min: [xs[lo], ys[lo], 0], max: [xs[hi], ys[hi], 0] },
    'z',
  );
  expect(box).not.toBeNull();
  const roi: Roi = {
    version: 1,
    site_id: 'notre-dame',
    source_run: 'notre-dame',
    frame: 'reconstruction',
    up_axis: 'z',
    min: box!.min,
    max: box!.max,
    created_at: '2026-10-17T12:00:00Z',
    created_by: 'a7-visual-test-fixture',
  };
  await page.route('**/api/sites/*/roi', async (route) => {
    expect(route.request().method()).toBe('GET');
    const site = decodeURIComponent(new URL(route.request().url()).pathname.split('/')[3]);
    await route.fulfill({ status: 200, json: { ...roi, site_id: site } });
  });
  await page.addInitScript(() => localStorage.setItem('qarau.lang', 'en'));
  await page.goto('/viewer/notre-dame');
  await page.waitForFunction(() => window.__qarau?.engine?.scene.getObjectByName('roi-box'), null, {
    timeout: 120_000,
  });
  const panel = page.getByRole('region', { name: en['roi.title'] });
  await expect(panel).toBeVisible();
  await expect(panel.getByText(en['roi.saved'], { exact: true })).toBeVisible();
  await expect(panel.getByRole('button', { name: en['roi.save'], exact: true })).toBeDisabled();
  const outline = await page.evaluate(() => {
    const helper = window.__qarau!.engine!.scene.getObjectByName('roi-box') as Box3Helper;
    return {
      min: helper.box.min.toArray(),
      max: helper.box.max.toArray(),
      visible: helper.visible,
    };
  });
  expect(outline).toEqual({ ...box, visible: true });
  const hide = panel.getByRole('button', { name: en['roi.hideOutside'], exact: true });
  await expect(hide).toBeEnabled();
  await hide.click();
  await expect(hide).toHaveAttribute('aria-pressed', 'true');
  await expect(page.getByRole('alert')).toHaveCount(0);
});
