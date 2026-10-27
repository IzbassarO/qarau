import { expect, it } from 'vitest';
import config from '../../playwright.config';

it('runs every spec at the fixed v8 desktop and phone viewports', () => {
  expect(config.projects).toEqual([
    { name: 'desktop', use: { viewport: { width: 1440, height: 900 } } },
    { name: 'phone', use: { viewport: { width: 390, height: 844 } } },
  ]);
});

// Owner rule: tests never check a server left running by someone else.
it('starts a fresh server for every e2e run and never reuses one', () => {
  const server = Array.isArray(config.webServer) ? config.webServer[0] : config.webServer;
  expect(server?.reuseExistingServer).toBe(false);
  expect(server?.command).toMatch(/--strictPort/);
  expect(server?.url).toBe(config.use?.baseURL);
});
