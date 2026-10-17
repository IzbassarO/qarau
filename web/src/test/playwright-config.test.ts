import { expect, it } from 'vitest';
import config from '../../playwright.config';

// Owner rule: tests never check a server left running by someone else.
it('starts a fresh server for every e2e run and never reuses one', () => {
  const server = Array.isArray(config.webServer) ? config.webServer[0] : config.webServer;
  expect(server?.reuseExistingServer).toBe(false);
  expect(server?.command).toMatch(/--strictPort/);
  expect(server?.url).toBe(config.use?.baseURL);
});
