// A returning bidder bids from the keyboard (brief 9.4, NFR-35): the field takes an amount,
// Enter submits, the assertive region announces the outcome, and focus stays in the field after
// a result that keeps it (a rejected bid).
import { expect, test } from '@playwright/test';

import { enIN } from '../src/strings/en-IN';
import { newAccount, newAuction, signInUi, waitForStatus, waitForLive } from './fixtures/stack';

test('Enter submits a bid and focus stays in the field after a rejection', async ({ browser, request }) => {
  test.setTimeout(120_000);
  const seller = await newAccount(request, 'seller', 'seller');
  const alice = await newAccount(request, 'alice');
  const id = await newAuction(request, seller, { endInS: 600, startingPrice: 900000, minIncrement: 50000, reservePrice: null });
  await waitForStatus(request, id, 'live');

  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  const errors: string[] = [];
  page.on('console', (m) => m.type() === 'error' && errors.push(m.text()));
  page.on('pageerror', (e) => errors.push(e.message));
  await signInUi(page, alice);
  await page.goto(`/auctions/${id}`);
  await waitForLive(page);

  const field = page.getByLabel(enIN.roomUi.bid.fieldLabel);
  await field.focus();
  await expect(field).toBeFocused();
  // a below-minimum amount, submitted with Enter: the server rejects it and the field keeps focus
  await field.fill('8000');
  await page.keyboard.press('Enter');
  const assertive = page.locator('#live-assertive');
  await expect(assertive).toHaveText(`${enIN.announce.notPlaced(enIN.errors.BID_TOO_LOW)} ${enIN.announce.minimumNow('₹9,000')}`, { timeout: 10_000 });
  await expect(field).toBeFocused();

  // a valid amount at the minimum: Enter submits and the bid is accepted
  await field.fill('9,000');
  await page.keyboard.press('Enter');
  await expect(assertive).toHaveText(enIN.room.accepted('₹9,000'), { timeout: 10_000 });

  expect(errors).toEqual([]);
  await ctx.close();
});
