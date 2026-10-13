// Close 4004 (session revoked: logout, password reset or suspension). The room keeps its last
// snapshot and polls anonymously from then on; no sentence renders with a value missing.
import { expect, test } from '@playwright/test';

import { enIN } from '../src/strings/en-IN';
import { newAccount, newAuction, resetPassword, restBid, signInUi, waitForStatus } from './fixtures/stack';

test('after 4004 the room keeps its snapshot and polls anonymously', async ({ browser, request }) => {
  test.setTimeout(120_000);
  const seller = await newAccount(request, 'seller', 'seller');
  const alice = await newAccount(request, 'alice');
  const bob = await newAccount(request, 'bob');
  const carol = await newAccount(request, 'carol');
  const id = await newAuction(request, seller, { endInS: 600, startingPrice: 900000, minIncrement: 50000, reservePrice: null });
  await waitForStatus(request, id, 'live');
  await (await restBid(request, bob, id, 900000)).json();

  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  await signInUi(page, alice);
  await page.goto(`/auctions/${id}`);
  await expect(page.getByText(/Connected,/)).toBeVisible({ timeout: 10_000 });

  // a password reset revokes every session: this page's socket closes 4004
  await resetPassword(request, alice);
  await expect(page.getByText(enIN.roomUi.banner.signedOut, { exact: true })).toBeVisible({ timeout: 20_000 });

  // the last snapshot stays: price, leader and the next minimum, status "Updates every 2 s"
  const main = page.getByRole('main');
  await expect(main.getByText('₹9,000', { exact: true }).first()).toBeVisible(); // price block or ledger, not the lot facts
  await expect(page.getByText(/^Updates every 2 s, /)).toBeVisible();
  await expect(page.getByText(enIN.roomUi.status.connecting, { exact: true })).toHaveCount(0);

  // anonymous polling keeps it current: another bidder's bid shows within a couple of polls
  const next = await restBid(request, carol, id, 950000);
  expect(next.ok()).toBe(true);
  await expect(main.getByText('₹9,500', { exact: true }).first()).toBeVisible({ timeout: 10_000 });

  // no sentence with its amount or time missing ("can be .", "at least .")
  expect(await main.innerText()).not.toMatch(/ \.(\s|$)/);
  await ctx.close();
});
