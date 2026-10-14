// The Phase 10 done check (UC-01), two real bidders on two screens: alternating bids, an outbid,
// a maximum that answers the next bid, a soft-close extension seen on both screens, the close,
// and the won/lost outcomes. Both ledgers end on the same sequence of bids.
import { expect, test, type Page } from '@playwright/test';

import { enIN } from '../src/strings/en-IN';
import { bidInLastWindow, newAccount, newAuction, restBid, signInUi, waitForStatus, waitForLive } from './fixtures/stack';

const r = enIN.roomUi;

async function ledgerSeqs(page: Page): Promise<string[]> {
  const cells = await page.locator('tbody tr td:first-child').allInnerTexts();
  return cells.filter((c) => /^\d+$/.test(c.trim())).map((c) => c.trim());
}

test('two bidders: outbid, a maximum answers, a soft-close extension on both screens, then won and lost', async ({ browser, request }) => {
  test.setTimeout(240_000);
  const seller = await newAccount(request, 'seller', 'seller');
  const alice = await newAccount(request, 'alice');
  const bob = await newAccount(request, 'bob');
  const id = await newAuction(request, seller, { endInS: 75, thresholdS: 20, extensionS: 20, startingPrice: 900000, minIncrement: 50000, reservePrice: 1025000 });
  await waitForStatus(request, id, 'live');

  const aliceCtx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const bobCtx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const alicePage = await aliceCtx.newPage();
  const bobPage = await bobCtx.newPage();
  const errors: string[] = [];
  for (const [who, p] of [['alice', alicePage], ['bob', bobPage]] as const) {
    p.on('console', (m) => m.type() === 'error' && errors.push(`${who} ${m.text()}`));
    p.on('pageerror', (e) => errors.push(`${who} ${e.message}`));
  }
  await signInUi(alicePage, alice);
  await signInUi(bobPage, bob);
  await alicePage.goto(`/auctions/${id}`);
  await waitForLive(alicePage);
  await bobPage.goto(`/auctions/${id}`);
  await waitForLive(bobPage);

  const aliceAssertive = alicePage.locator('#live-assertive');
  const bobAssertive = bobPage.locator('#live-assertive');

  // alice bids the minimum from the keyboard and leads
  const field = alicePage.getByLabel(r.bid.fieldLabel);
  await field.fill('9,000');
  await alicePage.keyboard.press('Enter');
  await expect(aliceAssertive).toHaveText(enIN.room.accepted('₹9,000'), { timeout: 10_000 });

  // bob outbids her; alice's assertive says so, from the outbid fields alone (no bidder named)
  await (await restBid(request, bob, id, 950000)).json();
  await expect(aliceAssertive).toContainText("You've been outbid at ₹9,500", { timeout: 10_000 });

  // alice sets a maximum that answers bob at once
  await alicePage.getByRole('button', { name: r.proxy.setAction, exact: true }).click();
  await alicePage.getByLabel(r.proxy.fieldLabel).fill('15,000');
  await alicePage.getByRole('button', { name: r.proxy.confirm }).click();
  await expect(aliceAssertive).toContainText('Your maximum of ₹15,000 is set', { timeout: 10_000 });
  await expect(alicePage.getByText(r.proxy.only)).toBeVisible();
  // fix 1: the maximum answered bob at once, so her price block shows she leads, not the outbid treatment
  await expect(alicePage.getByText(/You're the highest bidder with bid/)).toBeVisible({ timeout: 10_000 });
  // fix 4: the row her maximum placed is labelled "You" by alias, not only her manual rows
  const autoRow = alicePage.locator('tbody tr').filter({ hasText: r.ledger.byMaximum }).first();
  await expect(autoRow).toContainText(r.ledger.you, { timeout: 10_000 });

  // bob bids again; the maximum answers one step above, so bob is outbid on his own screen
  await (await restBid(request, bob, id, 1050000)).json();
  await expect(bobAssertive).toContainText("You've been outbid", { timeout: 10_000 });

  // a bid in the last 20 s extends the clock; both screens hear the extension
  await bidInLastWindow(request, bob, id, 1150000, 20);
  await expect(alicePage.locator('#live-polite')).toContainText('Extended by', { timeout: 15_000 });
  await expect(bobPage.locator('#live-polite')).toContainText('Extended by', { timeout: 15_000 });

  // let it close; alice's maximum kept her ahead, so she wins and bob loses
  await waitForStatus(request, id, 'closed_sold', 90_000);
  const finalPrice = enIN.roomUi.price; // for readability below
  await expect(alicePage.getByText(finalPrice.wonAt, { exact: true })).toBeVisible({ timeout: 15_000 });
  await expect(alicePage.getByRole('link', { name: /Pay ₹12,000/ })).toBeVisible();
  await expect(bobPage.getByText(finalPrice.soldFor, { exact: true })).toBeVisible({ timeout: 15_000 });
  await expect(bobPage.getByRole('link', { name: /Pay/ })).toHaveCount(0);

  // both screens list the same sequence of bids
  const [aliceSeqs, bobSeqs] = await Promise.all([ledgerSeqs(alicePage), ledgerSeqs(bobPage)]);
  expect(aliceSeqs.length).toBeGreaterThan(0);
  expect(aliceSeqs).toEqual(bobSeqs);

  expect(errors).toEqual([]);
  await aliceCtx.close();
  await bobCtx.close();
});
