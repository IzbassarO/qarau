import { execFileSync } from 'node:child_process';
import { readFile } from 'node:fs/promises';
import { resolve, basename, extname } from 'node:path';
import { expect, test } from '@playwright/test';
import fixture from '../src/test/fixtures/finding.json' with { type: 'json' };
import me from '../src/test/fixtures/me.json' with { type: 'json' };
import type { Manifest } from '../src/lib/bundle';

// Playwright collects each file once in its coordinator, then imports it in workers
// (TEST_WORKER_INDEX is set there). Both viewport projects share this one build.
// Serve its shell/assets through the existing fresh webServer: no second listening port,
// no gate-config changes, and real exported cloud/images still come from that server.
if (process.env.TEST_WORKER_INDEX === undefined) {
  execFileSync('pnpm', ['build'], { cwd: process.cwd(), stdio: 'inherit' });
}
const dist = resolve(process.cwd(), 'dist');

test('production evidence preview opens the first API finding with three outlined frames', async ({
  page,
}) => {
  await page.addInitScript(() => {
    localStorage.setItem('qarau.lang', 'en');
    sessionStorage.setItem('qarau.auth.session', 'Bearer evidence-e2e');
  });
  // Keep the real cloud and source images; only the findings/catalog responses are mocks.
  const response = await page.request.get('/api/runs/notre-dame-p3e/manifest.json');
  expect(response.ok()).toBe(true);
  const manifest = (await response.json()) as Manifest;
  const finding = {
    ...fixture.finding,
    evidence: fixture.finding.evidence.map((evidence) => ({
      ...evidence,
      image_url: `/api/runs/notre-dame-p3e/files/${manifest.frames[evidence.frame_id].image}`,
    })),
  };
  await page.route('**/api/me', (route) => route.fulfill({ json: me }));
  await page.route('**/api/findings/701/decisions', (route) => route.fulfill({ json: [] }));
  await page.route('**/api/inspections', (route) => route.fulfill({ json: [{ id: 41 }] }));
  await page.route('**/api/inspections/41/runs', (route) =>
    route.fulfill({
      json: [{ id: 'fixture-notre-dame-run', inspection_id: 41, run_dir: 'notre-dame-p3e' }],
    }),
  );
  await page.route('**/api/inspections/41/findings', (route) => route.fulfill({ json: [finding] }));
  await page.route('**/assets/*', async (route) => {
    const filename = basename(new URL(route.request().url()).pathname);
    const types: Record<string, string> = {
      '.js': 'text/javascript',
      '.css': 'text/css',
      '.woff2': 'font/woff2',
    };
    await route.fulfill({
      body: await readFile(resolve(dist, 'assets', filename)),
      contentType: types[extname(filename)] ?? 'application/octet-stream',
    });
  });
  await page.route('**/viewer/notre-dame-p3e?finding=*', async (route) => {
    await route.fulfill({
      body: await readFile(resolve(dist, 'index.html')),
      contentType: 'text/html',
    });
  });
  await page.goto('/viewer/notre-dame-p3e?finding=first');
  await page.waitForFunction(() => window.__qarau?.pointsLoaded, null, { timeout: 120_000 });
  const panel = page.getByRole('complementary', { name: 'Finding' });
  await expect(panel).toBeVisible();
  await expect(panel.getByRole('heading', { name: 'Corrosion' })).toBeVisible();
  for (const text of ['Critical', 'Confirmed', '1.2 m²'])
    await expect(panel.getByText(text, { exact: true })).toBeVisible();
  await expect(panel.locator('[data-testid="evidence-frame"]')).toHaveCount(3);
  await expect(panel.locator('svg polygon')).toHaveCount(3);
  for (const image of await panel.getByRole('img').all()) {
    await expect
      .poll(() => image.evaluate((image) => (image as HTMLImageElement).naturalWidth))
      .toBeGreaterThan(0);
  }
  await expect(panel).not.toContainText(/confidence|%|0\.987654|98\.7/i);
  await expect(panel.getByText('No decisions yet')).toBeVisible();
  for (const name of ['Confirm', 'Reject', 'Reclass'])
    await expect(panel.getByRole('button', { name, exact: true })).toBeVisible();
  await expect(panel.getByRole('textbox', { name: 'Comment' })).toBeVisible();
  // Visibility alone also passes for controls below a scroll container's bottom edge.
  await expect(panel.getByRole('textbox', { name: 'Comment' })).toBeInViewport({ ratio: 1 });
  for (const name of ['Confirm', 'Reject', 'Reclass'])
    await expect(panel.getByRole('button', { name, exact: true })).toBeInViewport({ ratio: 1 });
  await expect(panel.getByText('No decisions yet')).toBeInViewport({ ratio: 1 });
  await expect(page.getByRole('alert')).toHaveCount(0);
  await panel.getByRole('button', { name: 'Close finding' }).click();
  await expect(panel).toHaveCount(0);
  await page.goto('/viewer/notre-dame-p3e?finding=701');
  await page.waitForFunction(() => window.__qarau?.pointsLoaded, null, { timeout: 120_000 });
  await expect(panel.getByRole('heading', { name: 'Corrosion' })).toBeVisible();
  await expect(panel.locator('svg polygon')).toHaveCount(3);
});
