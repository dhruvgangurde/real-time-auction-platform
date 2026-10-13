// brief 11.5: at most 120 KB of WOFF2 before the load event, on every route, at 390 and 1440.
// Every /fonts/ response received before page.goto resolves (the load event) counts, preloaded
// or not. A route that pulls another weight above the fold fails here; the fix is the weight,
// never this number.
import { expect, test } from '@playwright/test';

export const FONT_BUDGET = 120 * 1024;
// grows as routes land: each slice-2 commit that adds a route adds it here
export const ROUTES = ['/', '/nothing-here', '/sign-in', '/register', '/account', '/help'];

for (const width of [1440, 390]) {
  for (const route of ROUTES) {
    test(`fonts before load on ${route} at ${width} stay under 120 KB`, async ({ browser }) => {
      const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 900 } });
      const page = await context.newPage();
      const fonts: { path: string; bytes: number }[] = [];
      let loaded = false;
      page.on('response', async (r) => {
        const path = new URL(r.url()).pathname;
        if (loaded || !path.startsWith('/fonts/')) return;
        fonts.push({ path, bytes: (await r.body()).length });
      });
      await page.goto(route); // resolves on the load event
      loaded = true;
      const total = fonts.reduce((n, f) => n + f.bytes, 0);
      console.log(`fonts ${route} @${width}: ${total} B (${fonts.map((f) => f.path.split('/').at(-1)).join(', ')})`);
      expect(total, fonts.map((f) => `${f.path} ${f.bytes}`).join('\n')).toBeLessThanOrEqual(FONT_BUDGET);
      expect(total).toBeGreaterThan(0);
      await context.close();
    });
  }
}
