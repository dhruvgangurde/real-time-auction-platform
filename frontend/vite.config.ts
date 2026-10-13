import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

// Development runs on 5173 and proxies the API (REST and the WebSocket upgrade) to the compose
// stack's web service, so the SPA, /api and the refresh cookie share one origin in development
// too (brief 12.5). Playwright never uses this server: e2e runs go against 8011 and its CSP.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': { target: 'http://localhost:8011', ws: true },
      '/media': { target: 'http://localhost:8011' },
    },
  },
  build: { outDir: 'dist', sourcemap: false },
  test: {
    environment: 'node',
    include: ['src/**/*.test.ts'],
  },
});
