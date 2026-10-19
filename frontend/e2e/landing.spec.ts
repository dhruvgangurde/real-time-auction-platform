// The landing at / (brief 8; slice 5) on 8011 with the real CSP, light and dark at 1440 and 390.
// Demo mode is off on the e2e stack: the hero shows the empty state, "Try to break it" says
// Unavailable, and no socket is ever opened (FR-28). The h1 is the largest contentful paint.
// The replay is routed: GET /demo points the hero at a real closed lot with a real ledger, which
// plays at its recorded pace, once, and again on "Replay again" (slice 5 Q2).
import { readdirSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

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

/** NFR-1's p95 in the newest committed results, as the strip shows it (src/landing/measured.ts). */
function newestNfr1(): string {
  const dir = fileURLToPath(new URL('../../loadtest/results/', import.meta.url));
  const newest = readdirSync(dir).filter((f) => f.endsWith('.json')).sort().at(-1)!;
  const file = JSON.parse(readFileSync(`${dir}${newest}`, 'utf8')) as { requirements: { id: string; measured: string }[] };
  return /^p95 (\d+\.\d{3} ms)/.exec(file.requirements.find((r) => r.id === 'NFR-1')!.measured)![1]!;
}
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
      await expect(measured.getByText(newestNfr1(), { exact: true })).toBeVisible();
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
  await expect(b.getByText(l.board.replay, { exact: true })).toBeVisible();
  // it plays by itself, at the recorded pace: the first bid, then the end
  await expect(b.locator('tbody tr')).not.toHaveCount(0, { timeout: 20_000 });
  // while it plays: the present tense
  await expect(b.getByText(l.board.currentBid, { exact: true })).toBeVisible();
  await expect(b.getByText(/ leads with bid №\d+\./)).toBeVisible();
  await expect(b.getByRole('button', { name: l.board.again })).toBeVisible({ timeout: 40_000 });

  // the four bids in the ledger, newest first; the proxy's two by maximum, never the maximum
  const rows = b.locator('tbody tr');
  await expect(rows).toHaveCount(4);
  await expect(rows.locator('td:first-child')).toHaveText(['4', '3', '2', '1']);
  await expect(rows.locator('td:nth-child(3)')).toHaveText(['₹10,500', '₹10,000', '₹9,500', '₹9,000']);
  await expect(b.locator('tbody td').getByText(l.board.ledger.byMaximum, { exact: true })).toHaveCount(2);
  await expect(b.getByText(l.board.sold(winner, '₹10,500', 4))).toBeVisible();
  // at the close: the outcome, never the present tense
  await expect(b.getByText(l.board.finalBid, { exact: true })).toBeVisible();
  await expect(b.getByText(l.board.won(winner, 4))).toBeVisible();
  await expect(b.getByText(l.board.currentBid, { exact: true })).toHaveCount(0);
  await expect(b.getByText(/ leads with bid №/)).toHaveCount(0);
  await expect(b.getByText(l.board.reserveMet)).toBeVisible();
  // the card (design/polish): how the newest bid landed, with its real number; what the run proved; its ledger
  await expect(b.getByRole('listitem').filter({ hasText: l.board.steps.decides(4) })).toBeVisible();
  for (const v of l.board.verified) await expect(b.getByRole('listitem').filter({ hasText: v })).toBeVisible();
  await expect(b.getByRole('link', { name: l.board.seeLedger })).toHaveAttribute('href', `/auctions/${replay.id}/bids`);
  expect(await b.locator('img').count()).toBe(0); // no photo in the card
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

test('the featured spread: in each row of small plates the photo tops align, whatever the captions (brief 8.5)', async ({ browser, request: api }) => {
  test.setTimeout(90_000);
  const seller = await newAccount(api, 'seller', 'seller');
  const t = tag();
  const long = 'hand-carved rosewood writing box with brass inlay, its original key and the fitted pen tray';
  const titles = Array.from({ length: 7 }, (_, i) => (i % 2 === 0 ? `Lot ${t} ${i}` : `Lot ${t} ${i}, ${long}`));
  for (const title of titles) await newAuction(api, seller, { title, startInS: 3600, endInS: 7200, reservePrice: null }); // upcoming; the last is newest
  for (const viewport of [
    { width: 1440, height: 900 },
    { width: 390, height: 844 },
  ]) {
    const context = await browser.newContext({ viewport });
    const page = await context.newPage();
    await page.goto('/');
    const lots = page.getByRole('region', { name: l.featured.title });
    await expect(lots.getByRole('link', { name: titles[6]! })).toBeVisible();
    await expect(lots.locator('figure')).toHaveCount(7);
    // the small plates (all but the first, large one), grouped into rows: figures in one row overlap vertically
    const plates = await lots.locator('figure').evaluateAll((figures) =>
      figures.slice(1).map((f) => {
        const box = f.getBoundingClientRect();
        return { top: box.top, bottom: box.bottom, image: f.querySelector('img')!.getBoundingClientRect().top };
      }),
    );
    const rows: (typeof plates)[] = [];
    for (const plate of plates) {
      const row = rows.find((r) => r.some((p) => p.top < plate.bottom && plate.top < p.bottom));
      if (row) row.push(plate);
      else rows.push([plate]);
    }
    if (viewport.width === 1440) expect(rows.some((r) => r.length > 1)).toBe(true); // rows of several plates to compare
    for (const row of rows) {
      const tops = row.map((p) => p.image);
      expect(Math.max(...tops) - Math.min(...tops), `${viewport.width}: image tops ${tops.join(', ')}`).toBeLessThanOrEqual(1);
    }
    await context.close();
  }
});
