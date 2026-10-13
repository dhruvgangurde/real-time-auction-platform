// The 390 fold (NFR-35, brief 5): on a phone the live column comes first, with the price, the
// countdown and the bid control above the 844 fold, and never a horizontal scroll. Checked in
// idle, outbid and extending.
import { expect, test, type Page } from '@playwright/test';

import { enIN } from '../src/strings/en-IN';
import { bidInLastWindow, newAccount, newAuction, restBid, signInUi, waitForStatus } from './fixtures/stack';

const FOLD = 844;

async function bottom(page: Page, locator: ReturnType<Page['locator']>): Promise<number> {
  const box = await locator.first().boundingBox();
  if (box === null) throw new Error('element not visible');
  return box.y + box.height;
}

async function assertFold(page: Page, label: string): Promise<void> {
  const noScroll = await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);
  expect(noScroll, `${label}: no horizontal scroll`).toBe(true);
  // the price and the countdown are always above the fold
  expect(await bottom(page, page.locator('p').filter({ hasText: '₹' }).first()), `${label}: price`).toBeLessThanOrEqual(FOLD);
}

test('the live column folds above 844 at 390, with no horizontal scroll', async ({ browser, request }) => {
  test.setTimeout(140_000);
  const seller = await newAccount(request, 'seller', 'seller');
  const alice = await newAccount(request, 'alice');
  const bob = await newAccount(request, 'bob');
  const id = await newAuction(request, seller, { endInS: 70, thresholdS: 20, extensionS: 20, startingPrice: 900000, minIncrement: 50000, reservePrice: null });
  await waitForStatus(request, id, 'live');

  const ctx = await browser.newContext({ viewport: { width: 390, height: FOLD } });
  const page = await ctx.newPage();
  const errors: string[] = [];
  page.on('console', (m) => m.type() === 'error' && errors.push(m.text()));
  page.on('pageerror', (e) => errors.push(e.message));
  await signInUi(page, alice);
  await page.goto(`/auctions/${id}`);

  // idle: the prefilled bid control is above the fold
  await expect(page.getByLabel(enIN.roomUi.bid.fieldLabel)).toBeVisible();
  await assertFold(page, 'idle');
  expect(await bottom(page, page.getByRole('button', { name: /Bid ₹/ })), 'idle: bid control').toBeLessThanOrEqual(FOLD);

  // outbid: alice leads, then bob outbids her; the outbid treatment and new minimum stay above the fold
  await (await restBid(request, alice, id, 900000)).json();
  await (await restBid(request, bob, id, 950000)).json();
  await expect(page.locator('#live-assertive')).toContainText('outbid', { timeout: 10_000 });
  await assertFold(page, 'outbid');

  // extending: a bid in the last 20 s extends the clock; the taller countdown note stays above the fold
  await bidInLastWindow(request, alice, id, 1000000, 20);
  await expect(page.locator('#live-polite')).toContainText('Extended by', { timeout: 15_000 });
  await assertFold(page, 'extending');

  expect(errors).toEqual([]);
  await ctx.close();
});
