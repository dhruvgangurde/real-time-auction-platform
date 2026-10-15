import react from '@vitejs/plugin-react';
import type { Plugin } from 'vite';
import { defineConfig } from 'vitest/config';

// The payment page is a document of its own (pay.html; brief 13 item 25), so nginx can give it
// its own CSP, and it is built on its own (vite.pay.config.ts), so no chunk is shared with the
// SPA. In development the same path is rewritten to it here, as nginx does on 8011.
const PAY_PATH = /^\/auctions\/[0-9a-f-]{36}\/pay(?:\?.*)?$/;
const payDocument = (): Plugin => ({
  name: 'pay-document',
  configureServer(server) {
    server.middlewares.use((req, _res, next) => {
      if (req.url !== undefined && PAY_PATH.test(req.url)) req.url = '/pay.html';
      next();
    });
  },
});

// Development runs on 5173 and proxies the API (REST and the WebSocket upgrade) to the compose
// stack's web service, so the SPA, /api and the refresh cookie share one origin in development
// too (brief 12.5). Playwright never uses this server: e2e runs go against 8011 and its CSP.
export default defineConfig({
  plugins: [react(), payDocument()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': { target: 'http://localhost:8011', ws: true },
      '/media': { target: 'http://localhost:8011' },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    manifest: true, // the manifest feeds scripts/chunk-budget.mjs
  },
  test: {
    environment: 'node',
    css: true,
    include: ['src/**/*.test.ts'],
  },
});
