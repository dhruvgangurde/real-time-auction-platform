// The shell proof (slice 1 part 9): on 8011 with the real CSP, in light and dark at 1440 and 390,
// with zero console errors, zero page errors, zero CSP violations and the expected headers;
// the refresh on load carries X-Requested-With and gets 204 for an anonymous visitor.
import { expect, test, type Page } from '@playwright/test';

import { enIN } from '../src/strings/en-IN';

const CSP =
  "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; " +
  "connect-src 'self' ws://localhost:8011; font-src 'self'; object-src 'none'; base-uri 'none'; " +
  "form-action 'self'; frame-ancestors 'none'";
const PAPER = { light: 'rgb(255, 255, 255)', dark: 'rgb(13, 20, 36)' } as const;
// brief 11.5: at most 120 KB of WOFF2 on first paint, the two preloaded files; the other weights
// load on use (Spectral Medium for titles) and are not part of the first-paint budget
const FONT_BUDGET = 120 * 1024;
const PRELOADED = ['/fonts/spectral/Spectral-Regular.woff2', '/fonts/anek-latin/AnekLatin.woff2'];

declare global {
  interface Window {
    cspViolations: string[];
  }
}

async function load(page: Page, path = '/') {
  const consoleErrors: string[] = [];
  const pageErrors: string[] = [];
  let fontBytes = 0;
  const fontFiles: string[] = [];
  page.on('console', (m) => {
    if (m.type() === 'error') consoleErrors.push(m.text());
  });
  page.on('pageerror', (e) => pageErrors.push(e.message));
  page.on('response', async (r) => {
    const path = new URL(r.url()).pathname;
    if (!path.startsWith('/fonts/')) return;
    fontFiles.push(path);
    if (PRELOADED.includes(path)) fontBytes += (await r.body()).length;
  });
  await page.addInitScript(() => {
    window.cspViolations = [];
    document.addEventListener('securitypolicyviolation', (e) => {
      window.cspViolations.push(`${e.violatedDirective} ${e.blockedURI}`);
    });
  });
  const refresh = page.waitForResponse((r) => r.url().endsWith('/api/v1/auth/refresh'));
  const response = await page.goto(path);
  await page.getByRole('main').waitFor();
  await page.evaluate(() => document.fonts.ready);
  return { response, refresh: await refresh, consoleErrors, pageErrors, fontBytes: () => fontBytes, fontFiles };
}

for (const theme of ['light', 'dark'] as const) {
  for (const viewport of [
    { width: 1440, height: 900 },
    { width: 390, height: 844 },
  ]) {
    test(`the shell loads clean in ${theme} at ${viewport.width}`, async ({ browser }) => {
      const context = await browser.newContext({ colorScheme: theme, viewport });
      const page = await context.newPage();
      const run = await load(page);

      // the document and its headers (brief 13 item 24)
      expect(run.response?.status()).toBe(200);
      const headers = run.response?.headers() ?? {};
      expect(headers['content-security-policy']).toBe(CSP);
      expect(headers['referrer-policy']).toBe('no-referrer');
      expect(headers['x-content-type-options']).toBe('nosniff');

      // the refresh on load (item 22): anonymous, so 204, with the header
      expect(run.refresh.status()).toBe(204);
      expect(run.refresh.request().headers()['x-requested-with']).toBe('fetch');

      // the shell from the catalogue, in the tokens' colours
      await expect(page.getByRole('heading', { level: 1 })).toHaveText(enIN.home.title);
      await expect(page.getByRole('link', { name: enIN.nav.signIn })).toBeVisible();
      expect(await page.evaluate(() => getComputedStyle(document.body).backgroundColor)).toBe(PAPER[theme]);

      // the fonts (brief 3.4, 11.5)
      const loaded = await page.evaluate(() => [...document.fonts].filter((f) => f.status === 'loaded').map((f) => f.family));
      expect(loaded).toContain('Spectral');
      expect(loaded).toContain('Anek Latin');
      for (const file of PRELOADED) expect(run.fontFiles).toContain(file);
      expect(run.fontBytes()).toBeLessThanOrEqual(FONT_BUDGET);
      expect(run.fontBytes()).toBeGreaterThan(0);

      // no horizontal scroll (brief 11.4), no errors, no CSP violations
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
      expect(run.consoleErrors).toEqual([]);
      expect(run.pageErrors).toEqual([]);
      expect(await page.evaluate(() => window.cspViolations)).toEqual([]);
      await context.close();
    });
  }
}

test('an unknown route renders the not-found page with the way back', async ({ page }) => {
  const run = await load(page, '/nothing-here');
  await expect(page.getByRole('heading', { level: 1 })).toHaveText(enIN.notFound.title);
  await expect(page.getByRole('link', { name: enIN.notFound.back })).toBeVisible();
  expect(run.consoleErrors).toEqual([]);
});

test('the skip link reaches main with the keyboard', async ({ page }) => {
  await load(page);
  await page.keyboard.press('Tab');
  await expect(page.getByRole('link', { name: enIN.app.skipToContent })).toBeFocused();
  await page.keyboard.press('Enter');
  expect(await page.evaluate(() => document.activeElement?.id)).toBe('main');
});
