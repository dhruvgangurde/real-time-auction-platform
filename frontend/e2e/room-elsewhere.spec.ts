// A viewer's bid placed outside this page (another tab or device, or REST) sends this page no
// ack. The room learns it led from an authenticated /live (you_lead), never from the alias
// alone (brief 13 item 15): after such a bid the price block shows the lead, not Outbid.
import { expect, test } from '@playwright/test';

import { enIN } from '../src/strings/en-IN';
import { newAccount, newAuction, restBid, signInUi, waitForStatus } from './fixtures/stack';

test("the viewer's own bid from elsewhere shows as the lead, not Outbid", async ({ browser, request }) => {
  test.setTimeout(120_000);
  const seller = await newAccount(request, 'seller', 'seller');
  const alice = await newAccount(request, 'alice');
  const bob = await newAccount(request, 'bob');
  const id = await newAuction(request, seller, { endInS: 600, startingPrice: 900000, minIncrement: 50000, reservePrice: null });
  await waitForStatus(request, id, 'live');

  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  await signInUi(page, alice);
  await page.goto(`/auctions/${id}`);
  await expect(page.getByLabel(enIN.roomUi.bid.fieldLabel)).toBeVisible();

  // alice leads from elsewhere, bob outbids her: this page shows the outbid treatment
  await (await restBid(request, alice, id, 900000)).json();
  await (await restBid(request, bob, id, 950000)).json();
  await expect(page.getByText(enIN.roomUi.price.outbid, { exact: true })).toBeVisible({ timeout: 10_000 });

  // alice bids again from elsewhere: no ack here, yet the page must show her lead
  await (await restBid(request, alice, id, 1000000)).json();
  await expect(page.getByText(/You're the highest bidder with bid №3/)).toBeVisible({ timeout: 10_000 });
  await expect(page.getByText(enIN.roomUi.price.outbid, { exact: true })).toHaveCount(0);
  await ctx.close();
});
