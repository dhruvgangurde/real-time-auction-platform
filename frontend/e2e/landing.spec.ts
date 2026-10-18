// The landing at / (brief 8; slice 5) on 8011 with the real CSP, light and dark at 1440 and 390.
// Demo mode is off on the e2e stack: the hero shows the empty state, "Try to break it" says
// Unavailable, and no socket is ever opened (FR-28). The h1 is the largest contentful paint.
// The replay is routed: GET /demo points the hero at a real closed lot with a real ledger, which
// plays at its recorded pace, once, and again on "Replay again" (slice 5 Q2).
import { expect, request, test, type Page } from '@playwright/test';

import { enIN } from '../src/strings/en-IN';
import { landingEnIN as l } from '../src/strings/landing-en-IN';
import { overview, replayLot, routeDemo, type ReplayLot } from './fixtures/landing';
import { API, newAccount, newAuction, signInUi, waitForStatus } from './fixtures/stack';

declare global {
  interface Window {
    cspViolations: string[];
  }
}

const tag = () => Math.random().toString(36).slice(2, 8);
let replay: ReplayLot;
let liveId: string;
let liveTitle: string;
let winner: string;

test.beforeAll(async () => {
  test.setTimeout(180_000);
  const api = await request.newContext();
  const t = tag();
  replay = await replayLot(api, `Leica ${t} M6 with 35 mm Summicron`);
  winner = String(((await (await api.get(`${API}/auctions/${replay.id}`)).json()) as { leader_alias: string }).leader_alias);
  const seller = await newAccount(api, 'seller', 'seller');
  liveTitle = `Rolleiflex ${t} 2.8F`;
  liveId = await newAuction(api, seller, { title: liveTitle, startInS: 2, endInS: 1800, reservePrice: null }); // the newest live lot
  await waitForStatus(api, liveId, 'live');
  await api.dispose();
});

/** Everything the landing must never do: a console or page error, a CSP violation, a socket. */
async function watch(page: Page) {
  const errors: string[] = [];
  const sockets: string[] = [];
  page.on('console', (m) => m.type() === 'error' && errors.push(m.text()));
  page.on('pageerror', (e) => errors.push(e.message));
  page.on('websocket', (ws) => sockets.push(ws.url()));
  await page.addInitScript(() => {
    window.cspViolations = [];
    document.addEventListener('securitypolicyviolation', (e) => window.cspViolations.push(`${e.violatedDirective} ${e.blockedURI}`));
  });
  return { errors, sockets };
}

/** The largest contentful paint so far: its element and size. */
async function lcp(page: Page): Promise<{ tag: string; text: string; size: number }> {
  return page.evaluate(
    () =>
      new Promise((resolve) => {
        new PerformanceObserver((list) => {
          const e = list.getEntries().at(-1) as PerformanceEntry & { element?: Element | null; size?: number };
          resolve({ tag: e.element?.tagName ?? '', text: (e.element?.textContent ?? '').slice(0, 60), size: e.size ?? 0 });
        }).observe({ type: 'largest-contentful-paint', buffered: true });
      }),
  );
}

const board = (page: Page) => page.getByRole('region', { name: l.board.label });

for (const theme of ['light', 'dark'] as const) {
  for (const viewport of [
    { width: 1440, height: 900 },
    { width: 390, height: 844 },
  ]) {
    test(`demo off: the landing loads clean in ${theme} at ${viewport.width}`, async ({ browser }) => {
      const context = await browser.newContext({ colorScheme: theme, viewport });
      const page = await context.newPage();
      const seen = await watch(page);
      await page.goto('/');

      await expect(page.getByRole('heading', { level: 1 })).toHaveText(enIN.home.lines.join(' '));
      // the hero: the empty state (GET /demo says enabled false), the newest live lot, Start bidding when signed out
      await expect(page.getByRole('heading', { name: l.board.emptyTitle })).toBeVisible();
      await expect(page.getByText(l.hero.ledeEmpty)).toBeVisible();
      await expect(page.getByRole('link', { name: l.hero.watch })).toHaveAttribute('href', `/auctions/${liveId}`);
      await expect(page.getByRole('link', { name: l.hero.start })).toHaveAttribute('href', '/register');
      // Try to break it: Unavailable, with no button
      await expect(page.getByText(l.break.unavailable.off)).toBeVisible();
      await expect(page.getByRole('button', { name: l.break.send })).toHaveCount(0);
      // Measured: the committed figures, each with its target
      const measured = page.getByRole('region', { name: l.measured.title });
      await expect(measured.getByText('0.128 ms')).toBeVisible();
      await expect(measured.getByText(l.measured.targets['NFR-1'])).toBeVisible();
      // the featured lots: real listings, the newest first
      const lots = page.getByRole('region', { name: l.featured.title });
      await expect(lots.getByRole('link', { name: liveTitle })).toHaveAttribute('href', `/auctions/${liveId}`);
      await expect(page.getByRole('region', { name: l.built.title })).toBeVisible();
      await expect(page.locator('main footer').getByRole('link', { name: l.built.footer.help })).toBeVisible();

      const paint = await lcp(page);
      console.log(`LCP demo off ${theme} @${viewport.width}: ${paint.tag} "${paint.text}" (${paint.size} px²)`);
      expect(paint.tag).toBe('H1');
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
      expect(seen.sockets).toEqual([]);
      expect(seen.errors).toEqual([]);
      expect(await page.evaluate(() => window.cspViolations)).toEqual([]);
      await context.close();
    });
  }
}

test('the hero replays a real run from its ledger, once, and again on Replay again', async ({ browser }) => {
  test.setTimeout(120_000);
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  const seen = await watch(page);
  await routeDemo(page, { overview: () => overview(replay.id) });
  await page.goto('/');

  const b = board(page);
  await expect(page.getByText(l.hero.ledeReplay)).toBeVisible();
  await expect(b.getByRole('heading', { name: replay.title })).toBeVisible();
  await expect(b.getByText(/^Replay of a real run, /)).toBeVisible();
  await expect(b.getByText(l.board.demoLot, { exact: true })).toBeVisible();
  // it plays by itself, at the recorded pace: the first bid, then the end
  await expect(b.locator('tbody tr')).not.toHaveCount(0, { timeout: 20_000 });
  await expect(b.getByRole('button', { name: l.board.again })).toBeVisible({ timeout: 40_000 });

  // the four bids in the ledger, newest first; the proxy's two by maximum, never the maximum
  const rows = b.locator('tbody tr');
  await expect(rows).toHaveCount(4);
  await expect(rows.locator('td:first-child')).toHaveText(['4', '3', '2', '1']);
  await expect(rows.locator('td:nth-child(3)')).toHaveText(['₹10,500', '₹10,000', '₹9,500', '₹9,000']);
  await expect(b.locator('tbody td').getByText(l.board.ledger.byMaximum, { exact: true })).toHaveCount(2);
  await expect(b.getByText(l.board.sold(winner, '₹10,500', 4))).toBeVisible();
  await expect(b.getByText(l.board.reserveMet)).toBeVisible();
  const paint = await lcp(page);
  console.log(`LCP replay @1440: ${paint.tag} "${paint.text}" (${paint.size} px²)`);
  expect(paint.tag).toBe('H1'); // the replay's photo stays smaller than the headline

  // Replay again: from the start, with no bids yet, and it plays through to the end again
  await b.getByRole('button', { name: l.board.again }).click();
  await expect(b.getByRole('button', { name: l.board.again })).toHaveCount(0);
  await expect(b.getByText(l.board.startingPrice)).toBeVisible();
  await expect(b.getByRole('button', { name: l.board.again })).toBeVisible({ timeout: 40_000 });
  await expect(rows).toHaveCount(4);

  expect(seen.sockets).toEqual([]);
  expect(seen.errors).toEqual([]);
  expect(await page.evaluate(() => window.cspViolations)).toEqual([]);
  await context.close();
});

test('with reduced motion the replay plays without animating', async ({ browser }) => {
  test.setTimeout(90_000);
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, reducedMotion: 'reduce' });
  const page = await context.newPage();
  await routeDemo(page, { overview: () => overview(replay.id) });
  await page.goto('/');
  const b = board(page);
  await b.scrollIntoViewIfNeeded();
  await expect(b.locator('tbody tr').first()).toBeVisible({ timeout: 20_000 });
  // sampled while the bids arrive: nothing on the page runs an animation or a transition
  const running: number[] = [];
  for (let i = 0; i < 12; i += 1) {
    running.push(await page.evaluate(() => document.getAnimations().length));
    await page.waitForTimeout(250);
  }
  expect(running.every((n) => n === 0), running.join(',')).toBe(true);
  await expect(b.getByRole('button', { name: l.board.again })).toBeVisible({ timeout: 40_000 });
  await context.close();
});

test('signed in, the second action is Browse auctions; the landing still opens no socket', async ({ page, request: api }) => {
  const bidder = await newAccount(api, 'bidder');
  const sockets: string[] = [];
  page.on('websocket', (ws) => sockets.push(ws.url()));
  await signInUi(page, bidder);
  await expect(page.getByRole('link', { name: l.hero.browse })).toHaveAttribute('href', '/auctions');
  await expect(page.getByRole('link', { name: l.hero.start })).toHaveCount(0);
  expect(sockets).toEqual([]);
});
