// The marigold highlight is a live moment only (brief 6): a new bid tints the changed digits and
// the new row, which fade as one; no cell keeps marigold after the band, and nothing is marigold
// once the room is terminal.
import { execFileSync } from 'node:child_process';

import { expect, test } from '@playwright/test';

import { ROOT, newAccount, newAuction, resetPassword, restBid, signInUi, waitForStatus } from './fixtures/stack';

const MARIGOLD = ['rgb(245, 180, 0)', 'rgb(255, 194, 26)']; // light, dark

async function marigoldCount(page: import('@playwright/test').Page): Promise<number> {
  return page.evaluate(
    (colors) => [...document.querySelectorAll('*')].filter((el) => colors.includes(getComputedStyle(el).backgroundColor)).length,
    MARIGOLD,
  );
}

test('the highlight fades to nothing, and no cell is marigold in a terminal room', async ({ browser, request }) => {
  test.setTimeout(140_000);
  const seller = await newAccount(request, 'seller', 'seller');
  const admin = await newAccount(request, 'admin', 'admin');
  const alice = await newAccount(request, 'alice');
  const bob = await newAccount(request, 'bob');
  const id = await newAuction(request, seller, { endInS: 600, reservePrice: null });
  await waitForStatus(request, id, 'live');

  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  await signInUi(page, alice);
  await page.goto(`/auctions/${id}`);
  await expect(page.getByLabel('Your bid in rupees')).toBeVisible();

  // a bid tints the row and digits, then the band fades: 2 s later nothing is marigold
  await (await restBid(request, bob, id, 900000)).json();
  await expect(page.locator('#live-polite')).toContainText('Current bid', { timeout: 10_000 });
  await page.waitForTimeout(2000);
  expect(await marigoldCount(page), 'marigold 2 s after a bid').toBe(0);

  // open a terminal room fresh: the final row must not mount with the highlight (brief 6)
  execFileSync('docker', ['compose', 'exec', '-T', 'api', 'python', '-m', 'app.cli', 'grant-role', admin.email, 'admin'], { cwd: ROOT, stdio: 'pipe' });
  await request.post(`http://localhost:8011/api/v1/admin/auctions/${id}/force-close`, { headers: { authorization: `Bearer ${admin.token}` }, data: { reason: 'highlight spec: settle now' } });
  await waitForStatus(request, id, 'closed_sold', 60_000);
  const fresh = await ctx.newPage();
  await fresh.goto(`/auctions/${id}`);
  await expect(fresh.getByRole('heading', { level: 1 })).toBeVisible();
  let maxSeen = 0;
  for (let i = 0; i < 20; i += 1) {
    maxSeen = Math.max(maxSeen, await marigoldCount(fresh));
    await fresh.waitForTimeout(60);
  }
  expect(maxSeen, 'marigold on a freshly loaded terminal room').toBe(0);
  await ctx.close();
});

test('the newest row fades as one: every cell shows the same background, during and after the band', async ({ browser, request }) => {
  test.setTimeout(120_000);
  const seller = await newAccount(request, 'seller', 'seller');
  const alice = await newAccount(request, 'alice');
  const bob = await newAccount(request, 'bob');
  const id = await newAuction(request, seller, { endInS: 600, reservePrice: null });
  await waitForStatus(request, id, 'live');
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  await signInUi(page, alice);
  await page.goto(`/auctions/${id}`);
  await expect(page.getByLabel('Your bid in rupees')).toBeVisible();

  // the background each cell shows: its own, or the row's behind it when its own is clear
  const cells = () =>
    page.evaluate(() => {
      const row = document.querySelector('tbody tr');
      if (row === null) return [];
      const clear = (c: string) => c === 'transparent' || /rgba\(.*,\s*0\)$/.test(c);
      const rowBg = getComputedStyle(row).backgroundColor;
      return [...row.querySelectorAll('td')].map((td) => {
        const own = getComputedStyle(td).backgroundColor;
        return clear(own) ? rowBg : own;
      });
    });

  await (await restBid(request, bob, id, 900000)).json();
  await expect(page.locator('tbody tr').first()).toContainText('₹9,000', { timeout: 10_000 });
  // a resize across the 700 px breakpoint mid-band (the decided-at cell hides on phones)
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(60);
  await page.setViewportSize({ width: 1440, height: 900 });
  for (let i = 0; i < 25; i += 1) {
    const seen = await cells();
    expect(new Set(seen).size, `cells at sample ${i}: ${seen.join(' | ')}`).toBe(1);
    await page.waitForTimeout(80);
  }
  await ctx.close();
});

test('the band never replays: not after a refused bid, not on a fresh load, not after a 4004 close', async ({ browser, request }) => {
  test.setTimeout(150_000);
  const seller = await newAccount(request, 'seller', 'seller');
  const alice = await newAccount(request, 'alice');
  const bob = await newAccount(request, 'bob');
  const id = await newAuction(request, seller, { endInS: 600, reservePrice: null });
  await waitForStatus(request, id, 'live');
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  await signInUi(page, alice);
  await page.goto(`/auctions/${id}`);
  await expect(page.getByLabel('Your bid in rupees')).toBeVisible();

  const marigold = (p: typeof page, selector: string) =>
    p.evaluate(
      ([sel, colors]) => [...document.querySelectorAll(sel)].filter((el) => colors.includes(getComputedStyle(el).backgroundColor)).length,
      [selector, MARIGOLD] as const,
    );
  /** the most marigold elements seen over about 1.5 s */
  const watch = async (p: typeof page, selector: string) => {
    let most = 0;
    for (let i = 0; i < 25; i += 1) {
      most = Math.max(most, await marigold(p, selector));
      await p.waitForTimeout(60);
    }
    return most;
  };

  // a bid after load starts a band on row 1 and the price; let it finish
  await (await restBid(request, bob, id, 900000)).json();
  await expect(page.locator('tbody tr').first()).toContainText('₹9,000', { timeout: 10_000 });
  await page.waitForTimeout(1500);
  expect(await marigold(page, 'tbody tr, tbody td')).toBe(0);

  // a pending bid that is refused: the same sequence, so no band
  await page.getByLabel('Your bid in rupees').fill('8,000');
  await page.keyboard.press('Enter');
  await expect(page.locator('#live-assertive')).toContainText('Bid not placed', { timeout: 10_000 });
  expect(await watch(page, 'tbody tr, tbody td'), 'ledger after a refused bid').toBe(0);

  // a fresh page load of the same room: the first snapshot never starts a band
  const fresh = await ctx.newPage();
  await fresh.goto(`/auctions/${id}`);
  await expect(fresh.locator('tbody tr').first()).toContainText('₹9,000', { timeout: 10_000 });
  expect(await watch(fresh, 'tbody tr, tbody td'), 'ledger on a fresh load').toBe(0);
  await fresh.close();

  // the session is revoked: the socket closes 4004, the room keeps its rows and polls
  await resetPassword(request, alice);
  await expect(page.getByText('You were signed out.', { exact: true })).toBeVisible({ timeout: 20_000 });
  expect(await watch(page, 'tbody tr, tbody td'), 'ledger after 4004').toBe(0);
  expect(await watch(page, 'main p span'), 'price digits after 4004').toBe(0);
  await ctx.close();
});
