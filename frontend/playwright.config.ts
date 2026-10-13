import { defineConfig } from '@playwright/test';

// Every e2e run goes against the web service on 8011 (nginx with the real CSP and headers), never
// the Vite dev server. Build first: npm run build. The stack must be up: docker compose up -d.
export default defineConfig({
  testDir: 'e2e',
  fullyParallel: false,
  retries: 0,
  reporter: 'list',
  use: {
    baseURL: 'http://localhost:8011',
    channel: 'chrome',
    trace: 'retain-on-failure',
  },
});
