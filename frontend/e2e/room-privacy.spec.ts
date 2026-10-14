// A proxy maximum and the reserve are never exposed to anyone but their owner (BR-7, FR-51,
// BR-6). Amendment 2: alice's maximum (₹12,345.67) is not a valid bid amount (not the start plus
// a multiple of the increment) and stays more than one increment above bob's highest bid for the
// whole run, so her proxy always answers one step above bob and never bids at its limit
// (_core.lua resolve: target = min(max, floor)). Across the run, bob's DOM, every response body
// and websocket frame his browser receives, and his console carry neither the reserve amount nor
// alice's maximum.
import { expect, test } from '@playwright/test';

import { enIN } from '../src/strings/en-IN';
import { newAccount, newAuction, restBid, restProxy, signInUi, waitForStatus, waitForLive } from './fixtures/stack';

const ALICE_MAX = 1234567; // ₹12,345.67: not start (900000) + k×50000
const RESERVE = 1025000; // ₹10,250
// the paise and the grouped-rupee forms of each secret, plus a short grouped prefix
const SECRETS = ['1234567', '12,345', '1025000', '10,250'];

test('bob never sees the reserve amount or alice’s maximum, anywhere', async ({ browser, request }) => {
  test.setTimeout(140_000);
  const seller = await newAccount(request, 'seller', 'seller');
  const alice = await newAccount(request, 'alice');
  const bob = await newAccount(request, 'bob');
  const id = await newAuction(request, seller, { endInS: 600, startingPrice: 900000, minIncrement: 50000, reservePrice: RESERVE });
  await waitForStatus(request, id, 'live');

  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  const seen: string[] = [];
  const errors: string[] = [];
  page.on('console', (m) => seen.push(`console ${m.text()}`));
  page.on('pageerror', (e) => errors.push(e.message));
  page.on('websocket', (ws) => ws.on('framereceived', (f) => typeof f.payload === 'string' && seen.push(`ws ${f.payload}`)));
  page.on('response', async (r) => {
    if ((r.headers()['content-type'] ?? '').includes('application/json')) {
      try {
        seen.push(`body ${await r.text()}`);
      } catch {
        /* a body that cannot be read carries nothing */
      }
    }
  });
  await signInUi(page, bob);
  await page.goto(`/auctions/${id}`);
  await waitForLive(page);
  await expect(page.getByLabel(enIN.roomUi.bid.fieldLabel)).toBeVisible();

  const snapshot = async () => seen.push(`dom ${await page.locator('body').innerText()}`);

  // bob leads; alice arms a hidden maximum that answers one step above him each time
  await (await restBid(request, bob, id, 900000)).json();
  await (await restProxy(request, alice, id, ALICE_MAX)).json(); // proxy answers 950000
  await expect(page.locator('#live-assertive')).toContainText('outbid', { timeout: 10_000 });
  await snapshot();

  await (await restBid(request, bob, id, 1000000)).json(); // proxy answers 1050000, reserve met
  await expect(page.getByText(/Reserve met/)).toBeVisible({ timeout: 10_000 });
  await snapshot();

  await (await restBid(request, bob, id, 1100000)).json(); // proxy answers 1150000; still 2 steps below the max
  await page.waitForTimeout(1500);
  await snapshot();

  // the ledger is open and shows the automatic answers "by maximum" without their owner's limit
  await expect(page.getByText(enIN.roomUi.ledger.byMaximum).first()).toBeVisible();
  await snapshot();

  expect(errors).toEqual([]);
  const hits = seen.filter((line) => SECRETS.some((s) => line.includes(s)));
  expect(hits, hits.slice(0, 5).join('\n---\n')).toEqual([]);
  // fix 3 (BR-6): a bidder sees only "Reserve met", never which bid met it ("met with/at bid №N")
  const metLeaks = seen.filter((line) => /met (with|at) bid/i.test(line));
  expect(metLeaks, metLeaks.slice(0, 3).join(' / ')).toEqual([]);
});
