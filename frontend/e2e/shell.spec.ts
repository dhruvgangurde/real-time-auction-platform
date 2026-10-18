// The shell proof (slice 1 part 9): on 8011 with the real CSP, in light and dark at 1440 and 390,
// with zero console errors, zero page errors, zero CSP violations and the expected headers;
// the refresh on load carries X-Requested-With and gets 204 for an anonymous visitor.
import { expect, test, type Page } from '@playwright/test';

import { enIN } from '../src/strings/en-IN';

const CSP =
  "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; " +
  "connect-src 'self' ws://localhost:8011; font-src 'self'; object-src 'none'; base-uri 'none'; " +
  "form-action 'self'; frame-ancestors 'none'";
const PAPER = { light: 'rgb(247, 244, 236)', dark: 'rgb(13, 20, 36)' } as const; // tokens.css --paper
// the font budget itself (every font before load, per route) is e2e/budgets.spec.ts
const PRELOADED = ['/fonts/spectral/Spectral-Regular.woff2', '/fonts/anek-latin/AnekLatin.woff2'];

declare global {
  interface Window {
    cspViolations: string[];
  }
}

async function load(page: Page, path = '/') {
  const consoleErrors: string[] = [];
  const pageErrors: string[] = [];
  const fontFiles: string[] = [];
  page.on('console', (m) => {
    if (m.type() === 'error') consoleErrors.push(m.text());
  });
  page.on('pageerror', (e) => pageErrors.push(e.message));
  page.on('response', (r) => {
    const path = new URL(r.url()).pathname;
    if (path.startsWith('/fonts/')) fontFiles.push(path);
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
  return { response, refresh: await refresh, consoleErrors, pageErrors, fontFiles };
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
      await expect(page.getByRole('heading', { level: 1 })).toHaveText(enIN.home.lines.join(' '));
      await expect(page.getByRole('link', { name: enIN.nav.signIn })).toBeVisible();
      expect(await page.evaluate(() => getComputedStyle(document.body).backgroundColor)).toBe(PAPER[theme]);

      // the fonts (brief 3.4, 11.5)
      const loaded = await page.evaluate(() => [...document.fonts].filter((f) => f.status === 'loaded').map((f) => f.family));
      expect(loaded).toContain('Spectral');
      expect(loaded).toContain('Anek Latin');
      for (const file of PRELOADED) expect(run.fontFiles).toContain(file);

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

test('at 390, signed in with an unread badge and the Sell icon, the header is one line: the wordmark never wraps, every icon-only link keeps its name, the account is named by the full name', async ({ browser, request }) => {
  test.setTimeout(90_000);
  const { API, bearer, newAccount, newAuction, restBid, signInUi, waitForStatus } = await import('./fixtures/stack');
  const name = 'AnanthaPadmanabhanKrishnaswamy'; // the email is built from it: no spaces
  const seller = await newAccount(request, 'seller', 'seller');
  const viewer = await newAccount(request, name, 'seller');
  const other = await newAccount(request, 'other');
  const id = await newAuction(request, seller, { startInS: 2, endInS: 600, reservePrice: null });
  await waitForStatus(request, id, 'live');
  await (await restBid(request, viewer, id, 900000)).json();
  await (await restBid(request, other, id, 950000)).json(); // the viewer is outbid: an unread row
  await expect
    .poll(async () => ((await (await request.get(`${API}/notifications?unread=true`, { headers: bearer(viewer) })).json()) as { items: unknown[] }).items.length, { timeout: 20_000 })
    .toBeGreaterThan(0);

  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 } });
  const page = await ctx.newPage();
  await signInUi(page, viewer);
  const header = page.getByRole('banner');
  // every icon-only control keeps an accessible name (amendment B); the account's is the user's name
  for (const link of [enIN.nav.notificationsLabel(1), enIN.nav.watchlist, enIN.nav.sell, name]) {
    await expect(header.getByRole('link', { name: link, exact: true })).toBeVisible();
  }
  await expect(header.getByRole('button', { name: enIN.nav.themeLabel(enIN.nav.themes.system, enIN.nav.themes.light) })).toBeVisible();
  const box = await page.evaluate(() => {
    const mark = document.querySelector('header a') as HTMLElement;
    const lineHeight = parseFloat(getComputedStyle(mark).lineHeight);
    return {
      markLines: Math.round(mark.getBoundingClientRect().height / lineHeight),
      // icons only: no visible words in the nav at 390
      words: (document.querySelector('header nav') as HTMLElement).innerText.trim(),
      header: document.querySelector('header')!.getBoundingClientRect().height,
      overflow: document.documentElement.scrollWidth - window.innerWidth,
    };
  });
  expect(box).toEqual({ markLines: 1, words: '1', header: 57, overflow: 0 }); // the badge's count; 56 px row + its 1 px rule
  await ctx.close();
});

test('at 390, signed out: Sign in keeps its word, the theme control its name, nothing overflows', async ({ browser }) => {
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 } });
  const page = await ctx.newPage();
  await page.goto('/');
  const header = page.getByRole('banner');
  await expect(header.getByRole('link', { name: enIN.nav.signIn, exact: true })).toHaveText(enIN.nav.signIn);
  await expect(header.getByRole('button', { name: enIN.nav.themeLabel(enIN.nav.themes.system, enIN.nav.themes.light) })).toBeVisible();
  await expect(header.getByRole('link', { name: enIN.nav.live })).toBeHidden(); // desktop only
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await ctx.close();
});
