import fs from 'node:fs';
import path from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import en from '../src/i18n/en.json' with { type: 'json' };
import { runUrl, type Manifest } from '../src/lib/bundle';
import { makeFarBundle } from './fixtures/far-layer/makeFarBundle';

const realManifestPath = path.resolve(
  process.env.QARAU_RUNS_DIR ?? '../data/runs',
  'notre-dame/web/manifest.json',
);

async function serveFarBundle(page: Page, { manifest, files }: ReturnType<typeof makeFarBundle>) {
  await page.addInitScript(() => localStorage.setItem('qarau.lang', 'en'));
  await page.route(`**/api/runs/${encodeURIComponent(manifest.run_id)}/manifest.json`, (route) =>
    route.fulfill({ json: manifest }),
  );
  // Use the loader's URL builder so every on-wire path keeps its /files/web/ prefix.
  for (const [relative, body] of files) {
    await page.route(`**${runUrl(manifest.run_id, relative)}`, (route) =>
      route.fulfill({
        body,
        contentType: relative.endsWith('.svg') ? 'image/svg+xml' : 'application/octet-stream',
      }),
    );
  }
}

async function layerState(page: Page) {
  return page.evaluate(() => {
    const engine = window.__qarau!.engine!;
    const far = engine.scene.getObjectByName('far') as
      | import('three').Points<import('three').BufferGeometry, import('three').PointsMaterial>
      | undefined;
    const object = engine.scene.children.find(
      (child) =>
        child.type === 'Points' &&
        child !== far &&
        (child as import('three').Points).geometry.hasAttribute('firstSeen'),
    ) as import('three').Points;
    return {
      available: engine.hasFarLayer(),
      visible: far?.visible,
      color: far?.material.color.getHexString(),
      vertexColors: far?.material.vertexColors,
      farCount: far?.geometry.getAttribute('position').count,
      objectCount: object.geometry.getAttribute('position').count,
      focus: engine.focus,
      target: engine.controls.target.toArray(),
      position: engine.camera.position.toArray(),
    };
  });
}

for (const run of ['demo', 'notre-dame']) {
  test(`far points remain separate and opt-in: ${run}`, async ({ page }) => {
    test.skip(run !== 'demo' && !fs.existsSync(realManifestPath), 'needs a Notre-Dame bundle');
    await page.addInitScript(() => localStorage.setItem('qarau.lang', 'en'));
    const errors: string[] = [];
    page.on('pageerror', (error) => errors.push(error.message));
    const servedManifest =
      run === 'demo'
        ? undefined
        : page.waitForResponse(
            (response) => new URL(response.url()).pathname === `/api/runs/${run}/manifest.json`,
          );
    await page.goto(`/viewer/${run}`);
    const manifestResponse = await servedManifest;
    if (manifestResponse) expect(manifestResponse.ok()).toBe(true);
    const manifest = (await manifestResponse?.json()) as Manifest | undefined;
    if (manifest) {
      await test.info().attach('served-manifest.json', {
        body: JSON.stringify({
          url: manifestResponse!.url(),
          points: manifest.points,
          far: manifest.far ?? null,
        }),
        contentType: 'application/json',
      });
      console.log(
        `Served ${run}: ${manifest.points.count} object points, ${manifest.far?.count ?? 0} far points`,
      );
    }
    await page.waitForFunction(() => window.__qarau?.pointsLoaded);
    await expect(page.getByRole('alert')).toHaveCount(0);
    await expect(page.getByText(en['error.webgl'])).toHaveCount(0);
    await expect(page.locator('canvas').locator('..')).toHaveCSS(
      'background-color',
      'rgb(0, 0, 0)',
    );
    const toggle = page.getByRole('button', { name: en['viewer.show.farPoints'] });
    const before = await layerState(page);
    if (manifest) {
      expect(before.objectCount).toBe(manifest.points.count);
      if (!manifest.far?.count) {
        await expect(toggle).toHaveCount(0);
        expect(before.available).toBe(false);
        expect(errors).toEqual([]);
        return;
      }
      expect(before.farCount).toBe(manifest.far.count);
    }
    await expect(toggle).toHaveAttribute('aria-pressed', 'false');
    expect(before).toMatchObject({
      available: true,
      visible: false,
      color: '6b727d',
      vertexColors: false,
    });
    await toggle.click();
    await expect(toggle).toHaveAttribute('aria-pressed', 'true');
    await expect.poll(() => layerState(page)).toEqual({ ...before, visible: true });
    await toggle.click();
    await expect.poll(() => layerState(page)).toEqual(before);
    expect(errors).toEqual([]);
  });
}

test('malformed far arrays warn while the real object cloud still renders', async ({ page }) => {
  const bundle = makeFarBundle({ brokenFar: true });
  const { manifest } = bundle;
  await serveFarBundle(page, bundle);
  await page.goto(`/viewer/${manifest.run_id}`);
  await page.waitForFunction(() => window.__qarau?.pointsLoaded);
  await expect(page.getByRole('alert')).toBeVisible();
  await expect(page.getByRole('alert')).toHaveText(en['viewer.far.errorBroken']);
  await expect(page.getByText(en['error.webgl'])).toHaveCount(0);
  expect(await layerState(page)).toMatchObject({
    available: false,
    objectCount: manifest.points.count,
  });
  await expect(page.getByRole('button', { name: en['viewer.show.farPoints'] })).toHaveCount(0);
});

test('valid synthetic far arrays show the opt-in toggle without a warning', async ({ page }) => {
  const bundle = makeFarBundle();
  const { manifest } = bundle;
  await serveFarBundle(page, bundle);
  await page.goto(`/viewer/${manifest.run_id}`);
  await page.waitForFunction(() => window.__qarau?.pointsLoaded);
  await expect(page.getByRole('alert')).toHaveCount(0);
  await expect(page.getByText(en['error.webgl'])).toHaveCount(0);
  const before = await layerState(page);
  expect(before).toMatchObject({
    available: true,
    visible: false,
    objectCount: manifest.points.count,
    farCount: manifest.far.count,
  });
  const toggle = page.getByRole('button', { name: en['viewer.show.farPoints'] });
  await expect(toggle).toHaveAttribute('aria-pressed', 'false');
  await toggle.click();
  await expect(toggle).toHaveAttribute('aria-pressed', 'true');
  await expect.poll(() => layerState(page)).toEqual({ ...before, visible: true });
});
