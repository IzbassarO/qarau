import { defineConfig } from '@playwright/test';

// Starts Vite with the dev runs middleware (QARAU_RUNS_DIR). Set QARAU_API_PROXY=http://localhost:8001
// to run the same test through the real API instead.
// Every run starts its own fresh Vite and stops it afterwards: never reuse a server that is
// already up (an old one on :5199 from an earlier session once served stale code to the tests).
// If the port is taken, Playwright fails with "already used" instead of testing that server.
const PORT = Number(process.env.QARAU_E2E_PORT ?? 5199);
const URL = `http://localhost:${PORT}`;
const GL = process.env.QARAU_GL_ARGS?.split(' ') ?? [
  '--use-angle=swiftshader',
  '--enable-unsafe-swiftshader',
];

export default defineConfig({
  testDir: 'e2e',
  outputDir: 'test-results/pw',
  timeout: 180_000,
  use: {
    baseURL: URL,
    viewport: { width: 1440, height: 900 },
    launchOptions: { args: GL },
  },
  webServer: {
    command: `pnpm exec vite --port ${PORT} --strictPort`,
    url: URL,
    reuseExistingServer: false,
    env: { QARAU_RUNS_DIR: process.env.QARAU_RUNS_DIR ?? '../data/runs' },
  },
});
