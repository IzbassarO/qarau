import { defineConfig } from '@playwright/test';

// Starts Vite with the dev runs middleware (QARAU_RUNS_DIR). Set QARAU_API_PROXY=http://localhost:8001
// to run the same test through the real API instead.
const GL = process.env.QARAU_GL_ARGS?.split(' ') ?? [
  '--use-angle=swiftshader',
  '--enable-unsafe-swiftshader',
];

export default defineConfig({
  testDir: 'e2e',
  outputDir: 'test-results/pw',
  timeout: 180_000,
  use: {
    baseURL: 'http://localhost:5199',
    viewport: { width: 1440, height: 900 },
    launchOptions: { args: GL },
  },
  webServer: {
    command: 'pnpm exec vite --port 5199 --strictPort',
    url: 'http://localhost:5199',
    reuseExistingServer: true,
    env: { QARAU_RUNS_DIR: process.env.QARAU_RUNS_DIR ?? '../../../../data/runs' },
  },
});
