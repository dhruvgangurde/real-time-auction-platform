import { fileURLToPath } from 'node:url';

import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

// The payment document's own build (brief 13 item 25), after the SPA's into the same dist/: one
// graph per document, so neither carries the other's code and neither entry is split into extra
// shared chunks. Its manifest is .vite/pay-manifest.json, read by scripts/chunk-budget.mjs.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: 'dist',
    emptyOutDir: false,
    sourcemap: false,
    manifest: '.vite/pay-manifest.json',
    rollupOptions: { input: fileURLToPath(new URL('pay.html', import.meta.url)) },
  },
});
