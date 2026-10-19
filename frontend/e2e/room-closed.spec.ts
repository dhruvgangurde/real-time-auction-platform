// A closed lot (closed_sold here; closed_no_sale and cancelled share the path, the terminal
// outcome): no bid panel for a visitor or a bidder who lost, no "Updates every 2 s", no poll once
// the room knows the lot is closed; the sold line stays. Signed out and signed in, 1440 and 390.
import { expect, test, type Page } from '@playwright/test';

import { enIN } from '../src/strings/en-IN';
import { API, bearer, newAccount, newAuction, restBid, signInUi, waitForStatus, type Account } from './fixtures/stack';

const b = enIN.roomUi.bid;

async function closedRoom(page: Page, id: string): Promise<void> {
  const polls: string[] = [];
  page.on('request', (r) => {
    if (r.url().endsWith(`/auctions/${id}/live`)) polls.push(r.url());
  });
  await page.goto(`/auctions/${id}`);
  await expect(page.getByText(enIN.roomUi.price.soldFor, { exact: true })).toBeVisible({ timeout: 15_000 });
  for (const text of [b.anonHeading, b.heading, b.signInToBid, b.registerPrompt]) {
    await expect(page.getByText(text, { exact: true })).toHaveCount(0);
  }
  await expect(page.getByText(/Next bid at least/)).toHaveCount(0);
  await expect(page.getByText(/Updates every/)).toHaveCount(0);
  // polling stops: the first snapshot said closed, so nothing more is asked
  const before = polls.length;
  await page.waitForTimeout(5_000);
  expect(polls.length, 'polls after the closed snapshot').toBe(before);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
}

test('a closed lot shows no bid panel and no poll line, signed out and signed in, at 1440 and 390', async ({ browser, request }) => {
  test.setTimeout(150_000);
  const seller = await newAccount(request, 'seller', 'seller');
  const admin = await newAccount(request, 'admin', 'admin');
  const alice = await newAccount(request, 'alice');
  const bob = await newAccount(request, 'bob');
  const id = await newAuction(request, seller, { endInS: 600, reservePrice: null });
  await waitForStatus(request, id, 'live');
  await (await restBid(request, bob, id, 900000)).json();
  await (await restBid(request, alice, id, 950000)).json(); // alice wins, bob loses
  await request.post(`${API}/admin/auctions/${id}/force-close`, { headers: bearer(admin), data: { reason: 'e2e: close the lot' } });
  await waitForStatus(request, id, 'closed_sold', 60_000);

  for (const viewport of [
    { width: 1440, height: 900 },
    { width: 390, height: 844 },
  ]) {
    const out = await browser.newContext({ viewport });
    await closedRoom(await out.newPage(), id);
    await out.close();

    const ctx = await browser.newContext({ viewport });
    const page = await ctx.newPage();
    await signInUi(page, bob as Account);
    await closedRoom(page, id);
    await ctx.close();
  }
});
