// Screenshots of slice 3 as built (Discover, the watchlist, notifications) for review, written to
// design/shots/05-slice3 with an index.md. Every frame is a real state driven through the API and
// the UI on 8011; loading and error are the only routed ones (a delayed and a refused list call).
// PNGs are git-ignored (design/shots/**/*.png); this spec regenerates them.
import { mkdirSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { expect, test, type Page } from '@playwright/test';

import { enIN } from '../src/strings/en-IN';
import { API, bearer, newAccount, newAuction, restBid, signInUi, waitForStatus, type Account } from './fixtures/stack';

const OUT = fileURLToPath(new URL('../../design/shots/05-slice3', import.meta.url));
mkdirSync(OUT, { recursive: true });

type Row = { state: string; file: string; polite: string; note?: string };
const rows: Row[] = [];
const tag = () => Math.random().toString(36).slice(2, 8);
const d = enIN.discover;

/** 1440 then 390 (light, or dark), full page unless `viewport`; `at390` runs before the phone frame. */
async function shoot(page: Page, state: string, theme: 'light' | 'dark', opts: { note?: string; at390?: () => Promise<void>; viewport?: boolean } = {}) {
  const polite = (await page.locator('#live-polite').textContent()) ?? '';
  for (const width of [1440, 390] as const) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 900 });
    if (width === 390) await opts.at390?.();
    await page.waitForTimeout(200);
    const file = `${state}-${width}-${theme}.png`;
    await page.screenshot({ path: `${OUT}/${file}`, fullPage: !opts.viewport });
    rows.push({ state, file, polite, ...(opts.note ? { note: opts.note } : {}) });
  }
  await page.setViewportSize({ width: 1440, height: 900 });
}

async function sellers(request: Parameters<typeof newAccount>[0], n: number): Promise<Account[]> {
  const out: Account[] = [];
  for (let i = 0; i < n; i += 1) out.push(await newAccount(request, 'seller', 'seller'));
  return out;
}

test('Discover: default, search, filters, closed, closing, empty, loading, error, more, unknown category, range', async ({ browser, request }) => {
  test.setTimeout(240_000);
  const [s1, s2, s3] = await sellers(request, 3);
  const bidder = await newAccount(request, 'bidder');
  const mix = tag();
  const closes = tag();
  const many = tag();
  // closing soon: one sold, one without a bid (no sale)
  const sold = await newAuction(request, s1!, { title: `Olympus ${closes} OM-1 with 50 mm f/1.8`, startInS: 2, endInS: 35, thresholdS: 10, extensionS: 10, reservePrice: null });
  await newAuction(request, s1!, { title: `Olympus ${closes} XA compact`, startInS: 2, endInS: 35, thresholdS: 10, extensionS: 10, reservePrice: null });
  // a mix for search: live with bids, live without, scheduled, in a child category
  const liveBids = await newAuction(request, s2!, { title: `Canon ${mix} AE-1 Program`, startInS: 2, endInS: 1800, reservePrice: null });
  await newAuction(request, s2!, { title: `Minolta ${mix} X-700`, startInS: 2, endInS: 5400, reservePrice: null });
  await newAuction(request, s2!, { title: `Yashica ${mix} Mat-124G`, startInS: 7200, endInS: 9000, reservePrice: null, categoryId: 2 });
  for (let i = 0; i < 21; i += 1) {
    await newAuction(request, [s1, s2, s3][i % 3]!, { title: `Box camera ${many} lot ${i + 1}`, startInS: 3600, endInS: 7200, reservePrice: null });
  }
  await waitForStatus(request, sold, 'live');
  await waitForStatus(request, liveBids, 'live');
  await (await restBid(request, bidder, sold, 900000)).json();
  await (await restBid(request, bidder, liveBids, 950000)).json();

  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  await page.goto('/auctions');
  await expect(page.locator('article').first()).toBeVisible();
  await shoot(page, '01-discover-default', 'light');

  await page.goto(`/auctions?q=${mix}`);
  await expect(page.locator('article')).toHaveCount(3);
  await shoot(page, '02-discover-search', 'light', { note: 'live with bids, live without, scheduled' });

  await page.goto(`/auctions?q=${mix}&category=electronics&status=live&status=scheduled&min=5000&max=50000`);
  await expect(page.locator('article')).toHaveCount(3);
  await shoot(page, '03-discover-filters', 'light', {
    note: 'filters applied; at 390 the disclosure is open',
    at390: () => page.getByRole('button', { name: d.filtersToggle(3) }).click(),
  });

  await page.goto(`/auctions?q=${many}`);
  await expect(page.locator('article')).toHaveCount(20);
  await page.getByRole('button', { name: d.showMore }).click();
  await expect(page.locator('article')).toHaveCount(21);
  await shoot(page, '08-discover-show-more', 'light', { note: 'page=2 in the URL, focus on the first new card' });

  await page.goto(`/auctions?q=zz${tag()}`);
  await expect(page.getByText(d.noMatch)).toBeVisible();
  await shoot(page, '05-discover-no-match', 'light');

  await page.goto('/auctions?category=no-such-category');
  await expect(page.getByText(d.unknownCategory)).toBeVisible();
  await shoot(page, '10-discover-unknown-category', 'light');

  await page.goto('/auctions?min=5000&max=100');
  await expect(page.getByText(d.range)).toBeVisible();
  await shoot(page, '11-discover-range-error', 'light', { note: 'no request is made' });

  await page.route('**/api/v1/auctions?*', async (r) => {
    await new Promise((res) => setTimeout(res, 4000));
    await r.continue().catch(() => undefined); // the page may have moved on (unrouted) meanwhile
  });
  await page.goto(`/auctions?q=${mix}`);
  await expect(page.getByRole('status').filter({ hasText: enIN.common.loading })).toBeAttached();
  await shoot(page, '06-discover-loading', 'light', { note: 'the list call routed with a 4 s delay' });
  await page.unroute('**/api/v1/auctions?*');

  await page.route('**/api/v1/auctions?*', (r) => r.fulfill({ status: 503, contentType: 'application/json', body: '{"error":{"code":"SERVICE_UNAVAILABLE","message":"down"}}' }));
  await page.goto(`/auctions?q=${mix}`);
  await expect(page.getByRole('button', { name: enIN.common.tryAgain })).toBeVisible({ timeout: 15_000 });
  await shoot(page, '07-discover-error', 'light', { note: 'the list call routed to 503' });
  await page.unroute('**/api/v1/auctions?*');

  // the two close-soon lots: Closing at zero, then their outcomes under the closed filter
  await page.goto(`/auctions?q=${closes}`);
  await expect(page.locator('article').first()).toContainText(enIN.cards.status.closing, { timeout: 60_000 });
  await shoot(page, '09-discover-closing', 'light', { note: 'live past zero: Closing, no countdown (G3)' });
  await waitForStatus(request, sold, 'closed_sold', 60_000);
  await page.goto(`/auctions?q=${closes}&status=closed_sold&status=closed_no_sale`);
  await expect(page.locator('article')).toHaveCount(2, { timeout: 20_000 });
  await shoot(page, '04-discover-closed', 'light', { note: 'sold for the final price; not sold' });
  await ctx.close();

  const dark = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: 'dark' });
  const dp = await dark.newPage();
  await dp.goto(`/auctions?q=${mix}`);
  await expect(dp.locator('article')).toHaveCount(3);
  await shoot(dp, '02-discover-search', 'dark');
  await dark.close();
});

test('Watchlist: signed-out Watch, every status, removed, empty, rollback; the room toggle', async ({ browser, request }) => {
  test.setTimeout(180_000);
  const [seller] = await sellers(request, 1);
  const bidder = await newAccount(request, 'bidder');
  const viewer = await newAccount(request, 'viewer');
  const t = tag();
  const closing = await newAuction(request, seller!, { title: `Zeiss ${t} Ikon Contessa`, startInS: 2, endInS: 30, thresholdS: 10, extensionS: 10, reservePrice: null });
  const live = await newAuction(request, seller!, { title: `Zeiss ${t} Contarex`, startInS: 2, endInS: 1800, reservePrice: null });
  const scheduled = await newAuction(request, seller!, { title: `Zeiss ${t} Super Ikonta`, startInS: 5400, endInS: 9000, reservePrice: null });
  const withdrawn = await newAuction(request, seller!, { title: `Zeiss ${t} Icarex`, startInS: 7200, endInS: 9000, reservePrice: null });
  await waitForStatus(request, closing, 'live');
  await waitForStatus(request, live, 'live');
  await (await restBid(request, bidder, closing, 900000)).json();
  await (await restBid(request, bidder, live, 900000)).json();
  for (const id of [closing, live, scheduled, withdrawn]) await request.put(`${API}/watchlist/${id}`, { headers: bearer(viewer) });
  await request.post(`${API}/auctions/${withdrawn}/cancel`, { headers: bearer(seller!) });

  const anon = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const ap = await anon.newPage();
  await ap.goto(`/auctions?q=${t}`);
  await ap.locator('article').first().getByRole('button', { name: enIN.watch.watch }).click();
  await ap.waitForURL(/\/sign-in$/);
  await shoot(ap, '12-watch-signed-out', 'light', { note: 'Watch while signed out: sign in, then back to the same URL; nothing added' });
  await anon.close();

  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  await signInUi(page, viewer);
  await waitForStatus(request, closing, 'closed_sold', 60_000);
  await page.goto('/watchlist');
  await expect(page.locator('article')).toHaveCount(4);
  await shoot(page, '13-watchlist', 'light', { note: 'sold, live with a countdown, scheduled, cancelled; ending soonest first' });

  await page.locator('article', { hasText: `Zeiss ${t} Super Ikonta` }).getByRole('button', { name: enIN.watch.watching }).click();
  await expect(page.getByRole('button', { name: enIN.watch.again })).toBeVisible();
  await shoot(page, '14-watchlist-removed', 'light', { note: 'kept with Watch again until the viewer leaves' });

  await page.route('**/api/v1/watchlist/*', (r) => r.fulfill({ status: 503, contentType: 'application/json', body: '{"error":{"code":"SERVICE_UNAVAILABLE","message":"down"}}' }));
  await page.getByRole('button', { name: enIN.watch.again }).click();
  await expect(page.getByText(enIN.watch.addFailed)).toBeVisible();
  await shoot(page, '16-watch-rolled-back', 'light', { note: 'PUT routed to 503: back to Watch again, with the message' });
  await page.unroute('**/api/v1/watchlist/*');

  await page.goto(`/auctions/${live}`);
  await expect(page.getByRole('button', { name: enIN.watch.watching })).toBeVisible();
  await shoot(page, '24-room-watching', 'light', { note: 'the toggle in the room, with the FR-73 note' });
  await ctx.close();

  const dark = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: 'dark' });
  const dp = await dark.newPage();
  await signInUi(dp, viewer);
  await dp.goto('/watchlist');
  await expect(dp.locator('article')).toHaveCount(3);
  await shoot(dp, '13-watchlist', 'dark');
  await dark.close();

  const fresh = await newAccount(request, 'fresh');
  const empty = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const ep = await empty.newPage();
  await signInUi(ep, fresh);
  await ep.goto('/watchlist');
  await expect(ep.getByText(enIN.watchlist.empty)).toBeVisible();
  await shoot(ep, '15-watchlist-empty', 'light');
  await empty.close();
});

test('Notifications: the bell, seven types, unread, mark all, empty, caught up, email choices', async ({ browser, request }) => {
  test.setTimeout(180_000);
  const [other] = await sellers(request, 1);
  const x = await newAccount(request, 'xena', 'seller'); // sells, bids and watches: seven types on one page
  const bob = await newAccount(request, 'bob');
  const t = tag();
  const short = { startInS: 2, endInS: 30, thresholdS: 10, extensionS: 10, reservePrice: null } as const;
  const sells = await newAuction(request, x, { ...short, title: `Praktica ${t} MTL 5` });
  const noSale = await newAuction(request, x, { ...short, title: `Praktica ${t} LTL` });
  const loses = await newAuction(request, other!, { ...short, title: `Kiev ${t} 88` });
  const wins = await newAuction(request, other!, { ...short, title: `Zenit ${t} E` });
  const watched = await newAuction(request, other!, { startInS: 2, endInS: 240, reservePrice: null, title: `Lubitel ${t} 166` });
  for (const id of [sells, noSale, loses, wins, watched]) await waitForStatus(request, id, 'live');
  await request.put(`${API}/watchlist/${watched}`, { headers: bearer(x) });
  await (await restBid(request, bob, sells, 900000)).json();
  await (await restBid(request, x, loses, 900000)).json();
  await (await restBid(request, bob, loses, 950000)).json();
  await (await restBid(request, x, wins, 900000)).json();
  for (const id of [sells, loses, wins]) await waitForStatus(request, id, 'closed_sold', 60_000);
  await waitForStatus(request, noSale, 'closed_no_sale', 60_000);
  await expect
    .poll(async () => ((await (await request.get(`${API}/notifications?limit=50`, { headers: bearer(x) })).json()) as { items: unknown[] }).items.length, { timeout: 60_000 })
    .toBeGreaterThanOrEqual(7);

  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  await signInUi(page, x);
  await page.goto('/auctions');
  await expect(page.getByRole('link', { name: /^Notifications, \d+ unread$/ })).toBeVisible();
  await shoot(page, '17-header-bell', 'light', { note: 'the unread count beside the bell; icon and count only at 390; viewport only', viewport: true });

  await page.goto('/notifications');
  await expect(page.locator('ol li')).toHaveCount(7);
  await shoot(page, '18-notifications', 'light', { note: 'outbid, ending soon, lost, won, payment due, sold, not sold' });

  await page.getByRole('button', { name: /^Mark as read: / }).first().click();
  await page.goto('/notifications?filter=unread');
  await expect(page.locator('ol li')).toHaveCount(6);
  await shoot(page, '19-notifications-unread', 'light', { note: 'one marked read, so six left' });

  await page.getByRole('button', { name: enIN.notificationsPage.markAll }).click();
  await expect(page.getByText(enIN.notificationsPage.caughtUp)).toBeVisible();
  await shoot(page, '22-notifications-all-read', 'light', { note: 'after Mark all as read: caught up, the bell without a count' });

  await page.goto('/notifications');
  await page.getByRole('heading', { name: enIN.emailPrefs.title }).scrollIntoViewIfNeeded();
  await shoot(page, '23-email-choices', 'light');
  await ctx.close();

  const dark = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: 'dark' });
  const dp = await dark.newPage();
  await signInUi(dp, x);
  await dp.goto('/notifications');
  await expect(dp.locator('ol li')).toHaveCount(7);
  await shoot(dp, '18-notifications', 'dark');
  await dark.close();

  const fresh = await newAccount(request, 'fresh');
  const empty = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const ep = await empty.newPage();
  await signInUi(ep, fresh);
  await ep.goto('/notifications');
  await expect(ep.getByText(enIN.notificationsPage.empty)).toBeVisible();
  await shoot(ep, '20-notifications-empty', 'light');
  await ep.goto('/notifications?filter=unread');
  await expect(ep.getByText(enIN.notificationsPage.caughtUp)).toBeVisible();
  await shoot(ep, '21-notifications-caught-up', 'light');
  await empty.close();
});

test.afterAll(() => {
  rows.sort((a, b) => a.state.localeCompare(b.state) || a.file.localeCompare(b.file));
  const esc = (s: string) => s.replace(/\|/g, '\\|').trim();
  const lines = [
    '# 05-slice3 shots',
    '',
    'Discover, the watchlist and notifications as built (`frontend/src/discover/`, `watch/`, `watchlist/`, `notifications/`), rendered from real state on 8011 by `frontend/e2e/slice3-shots.spec.ts`. Every frame is a real state driven through the API and the UI; only the loading and error frames route the list call (a 4 s delay, a 503). PNGs are git-ignored (`design/shots/**/*.png`); re-make them with `cd frontend && npm run e2e -- e2e/slice3-shots.spec.ts`.',
    '',
    'Light at 1440 and 390 for every state; Discover search, the watchlist and the notifications list also in dark. The last column is the polite live region at the moment of the shot.',
    '',
    '| State | File | Polite region |',
    '|---|---|---|',
    ...rows.map((r) => `| ${r.state}${r.note ? ` (${esc(r.note)})` : ''} | ${r.file} | ${r.polite ? `"${esc(r.polite)}"` : 'Nothing announced.'} |`),
    '',
    '## Not shot here',
    '',
    '- **Payment received and payment failed** rows need a payment, which arrives with the checkout slice; their sentences are covered by `src/notifications/rows.test.ts`.',
    '- **A new notification arriving** is motion (the count rising, the polite line); `e2e/notifications.spec.ts` checks it by poll and by the room frame.',
    '- **Rate limited** on Discover: the list endpoint has no limiter (G1), so the state cannot be reached from the real backend; the shared error path handles the code.',
    '',
  ];
  writeFileSync(`${OUT}/index.md`, lines.join('\n'));
});
