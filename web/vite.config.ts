import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import { devRuns } from './vite-dev-runs';

// Dev: /api/runs* is served from QARAU_RUNS_DIR, or proxied to the real API with QARAU_API_PROXY=1.
const proxy = process.env.QARAU_API_PROXY === '1';

export default defineConfig({
  plugins: [react(), tailwindcss(), ...(proxy ? [] : [devRuns()])],
  server: proxy ? { proxy: { '/api': 'http://localhost:8000' } } : {},
  test: { include: ['src/**/*.test.ts'] },
});
