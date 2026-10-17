import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import { devRuns } from './vite-dev-runs.ts';

// Dev: /api/runs* is served from QARAU_RUNS_DIR, or proxied to the real API with QARAU_API_PROXY=1.
const proxyEnv = process.env.QARAU_API_PROXY;
const proxy = !!proxyEnv && proxyEnv !== '0';
// QARAU_API_PROXY=1 -> http://localhost:8000; any other value is the target URL
const target = proxyEnv === '1' ? 'http://localhost:8000' : proxyEnv;

export default defineConfig({
  plugins: [react(), tailwindcss(), ...(proxy ? [] : [devRuns()])],
  server: proxy ? { proxy: { '/api': target! } } : {},
  test: {
    include: ['src/**/*.test.{ts,tsx}'],
    environment: 'jsdom',
    setupFiles: ['src/test/setup.ts'],
    // afterEach in declaration order: setup.ts unmounts (cleanup) before a test file
    // unstubs globals / restores mocks that the unmounting components still use.
    sequence: { hooks: 'list' },
  },
});
