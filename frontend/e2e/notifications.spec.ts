// Notifications (brief 9.1, 9.6; FR-70 to FR-74, UI-7, NFR-35) on 8011, with real rows from the
// stack: outbid (the bid task), ending soon (the 30 s beat over the watchlist, FR-73) and the
// close outcomes (won, payment due, lost, sold). The bell rises without a reload, and at once in a
// room through the live `notification` frame; one line per type; mark one and all read; the
// unread filter; "Show more"; email choices persist; empty states; 390, keyboard and dark.
import { expect, test, type APIRequestContext, type Page } from '@playwright/test';

import { enIN } from '../src/strings/en-IN';
import { API, bearer, newAccount, newAuction, restBid, signInUi, waitForLive, waitForStatus, type Account } from './fixtures/stack';

const p = enIN.notificationsPage;
const tag = () => Math.random().toString(36).slice(2, 8);

async function unreadOf(api: APIRequestContext, who: Account): Promise<{ type: string }[]> {
  const r = await api.get(`${API}/notifications?unread=true&limit=100`, { headers: bearer(who) });
  return ((await r.json()) as { items: { type: string }[] }).items;
}

/** The bell's count from its accessible name ("Notifications, 3 unread"), 0 without a count. */
async function bell(page: Page): Promise<number> {
  const name = (await page.getByRole('link', { name: /^Notifications/ }).getAttribute('aria-label')) ?? '';
  const m = /, (\d+) unread$/.exec(name);
  return m ? Number(m[1]) : 0;
}

function errorsOf(page: Page): string[] {
  const errors: string[] = [];
  page.on('console', (m) => m.type() === 'error' && errors.push(m.text()));
  page.on('pageerror', (e) => errors.push(e.message));
  return errors;
}

test('real rows: the bell rises without a reload, at once in a room, and every type has its line', async ({ browser, request }) => {
  test.setTimeout(180_000);
  const t = tag();
  const seller = await newAccount(request, 'seller', 'seller');
  const alice = await newAccount(request, 'alice');
  const bob = await newAccount(request, 'bob');
  const id = await newAuction(request, seller, { title: `Voigtlander ${t}`, startInS: 2, endInS: 50, thresholdS: 10, extensionS: 10, reservePrice: null });
  await waitForStatus(request, id, 'live');
  await request.put(`${API}/watchlist/${id}`, { headers: bearer(alice) }); // ending soon (FR-73)

  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  const errors = errorsOf(page);
  const pushed: string[] = [];
  page.on('websocket', (ws) =>
    ws.on('framereceived', (f) => typeof f.payload === 'string' && /"type":\s*"notification"/.test(f.payload) && pushed.push(f.payload)),
  );
  await signInUi(page, alice);
  await page.goto('/auctions');
  expect(await bell(page)).toBe(0);

  // alice is outbid: the stored row reaches the bell by the 30 s poll, no reload
  await (await restBid(request, alice, id, 900000)).json();
  await (await restBid(request, bob, id, 950000)).json();
  await expect.poll(() => bell(page), { timeout: 40_000 }).toBeGreaterThanOrEqual(1);
  await expect(page.locator('#live-polite')).toContainText(/New notification\. \d+ unread\./);

  // in the room when it closes: auction_lost is pushed live, so the bell moves well inside a poll
  await page.goto(`/auctions/${id}`);
  await waitForLive(page);
  await expect(page.getByText(enIN.roomUi.price.soldFor).first()).toBeVisible({ timeout: 70_000 });
  const before = (await unreadOf(request, alice)).length;
  await expect.poll(async () => (await unreadOf(request, alice)).some((n) => n.type === 'auction_lost'), { timeout: 20_000 }).toBe(true);
  await expect.poll(() => bell(page), { timeout: 6_000 }).toBeGreaterThanOrEqual(Math.max(before, 3));
  expect(pushed.some((f) => f.includes('auction_lost'))).toBe(true); // it came over the socket

  // one line per type: alice's three
  await page.goto('/notifications');
  await expect(page.getByRole('heading', { level: 1, name: p.title })).toBeVisible();
  const rows = page.locator('ol li');
  await expect(rows).toHaveCount(3);
  await expect(rows.filter({ hasText: p.labels.outbid })).toContainText(`You were outbid on Voigtlander ${t}. The current bid is ₹9,500.`);
  await expect(rows.filter({ hasText: p.labels.ending_soon })).toContainText(`Voigtlander ${t} ends soon, at`);
  await expect(rows.filter({ hasText: p.labels.auction_lost })).toContainText(`Voigtlander ${t} sold for ₹9,500. Your bid did not win.`);
  // a row links to its auction, and opening it marks it read
  await rows.filter({ hasText: p.labels.outbid }).getByRole('link').click();
  await expect(page).toHaveURL(new RegExp(`/auctions/${id}$`));
  await expect.poll(async () => (await unreadOf(request, alice)).some((n) => n.type === 'outbid')).toBe(false);
  expect(errors).toEqual([]);
  await ctx.close();

  // the winner's and the seller's lines
  for (const [who, lines] of [
    [bob, [`You won Voigtlander ${t} at ₹9,500.`, `₹9,500 for Voigtlander ${t}.`]],
    [seller, [`Voigtlander ${t} sold for ₹9,500.`]],
  ] as const) {
    const c = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const pg = await c.newPage();
    await signInUi(pg, who);
    await pg.goto('/notifications');
    for (const line of lines) await expect(pg.getByText(line)).toBeVisible();
    await c.close();
  }
});

test('mark one and all read, the unread filter, Show more; at 390, dark, from the keyboard', async ({ browser, request }) => {
  test.setTimeout(150_000);
  const t = tag();
  const seller = await newAccount(request, 'seller', 'seller');
  const alice = await newAccount(request, 'alice');
  const bob = await newAccount(request, 'bob');
  const id = await newAuction(request, seller, { title: `Exakta ${t}`, startInS: 2, endInS: 900, reservePrice: null, startingPrice: 100000, minIncrement: 1000 });
  await waitForStatus(request, id, 'live');
  // 22 outbids of alice, one stored row each; paced under the 10 bids a second per user (FR-44)
  let amount = 100000;
  for (let i = 0; i < 22; i += 1) {
    expect((await restBid(request, alice, id, amount)).ok()).toBe(true);
    expect((await restBid(request, bob, id, amount + 1000)).ok()).toBe(true);
    amount += 2000;
    await new Promise((r) => setTimeout(r, 150));
  }
  await expect.poll(async () => (await unreadOf(request, alice)).length, { timeout: 30_000 }).toBe(22);

  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, colorScheme: 'dark' });
  const page = await ctx.newPage();
  const errors = errorsOf(page);
  await signInUi(page, alice);
  await page.goto('/notifications');
  await expect(page.locator('body')).toHaveCSS('background-color', 'rgb(13, 20, 36)');
  await expect(page.getByRole('link', { name: 'Notifications, 22 unread' })).toBeVisible(); // the bell at 390
  await expect(page.locator('ol li')).toHaveCount(20);
  await page.getByRole('button', { name: p.showMore }).click();
  await expect(page.locator('ol li')).toHaveCount(22);
  await expect(page.getByRole('button', { name: p.showMore })).toHaveCount(0);

  // mark one read from the keyboard: the bell follows at once
  const first = page.getByRole('button', { name: /^Mark as read: / }).first();
  await first.focus();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('link', { name: 'Notifications, 21 unread' })).toBeVisible();
  await expect.poll(async () => (await unreadOf(request, alice)).length).toBe(21);

  // the unread filter is in the URL; mark all empties it
  await page.getByRole('link', { name: p.unread, exact: true }).click();
  await expect(page).toHaveURL(/\/notifications\?filter=unread$/);
  await expect(page.locator('ol li')).toHaveCount(20);
  await page.getByRole('button', { name: p.markAll }).click();
  await expect(page.getByRole('link', { name: 'Notifications', exact: true })).toBeVisible();
  await expect(page.getByText(p.caughtUp)).toBeVisible();
  expect(await unreadOf(request, alice)).toEqual([]);
  await expect(page.getByRole('button', { name: p.markAll })).toHaveAttribute('aria-disabled', 'true');
  expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);
  expect(errors).toEqual([]);
  await ctx.close();
});

test('a new account sees the empty states; email choices are saved and come back', async ({ page, request }) => {
  const viewer = await newAccount(request, 'viewer');
  await signInUi(page, viewer);
  await page.goto('/notifications');
  await expect(page.getByText(p.empty)).toBeVisible();
  await page.goto('/notifications?filter=unread');
  await expect(page.getByText(p.caughtUp)).toBeVisible();

  const e = enIN.emailPrefs;
  const outbid = page.getByLabel(e.labels.outbid);
  await expect(outbid).toBeChecked(); // every type is on until changed
  await outbid.uncheck();
  await page.getByRole('button', { name: e.save }).click();
  await expect(page.getByText(e.saved)).toBeVisible();
  await page.reload();
  await expect(page.getByLabel(e.labels.outbid)).not.toBeChecked();
  await expect(page.getByLabel(e.labels.payment_due)).toBeChecked();
  const prefs = (await (await request.get(`${API}/notifications/preferences`, { headers: bearer(viewer) })).json()) as { email: Record<string, boolean> };
  expect(prefs.email.outbid).toBe(false);
  expect(prefs.email.ending_soon).toBe(true);
});
