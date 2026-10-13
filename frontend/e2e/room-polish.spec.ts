// Polish (brief 5): the ledger's amount and decided-at headers sit right, over their right-aligned
// columns; the lot title balances across lines.
import { expect, test } from '@playwright/test';

import { enIN } from '../src/strings/en-IN';
import { newAccount, newAuction, restBid, signInUi, waitForStatus } from './fixtures/stack';

const align = (page: import('@playwright/test').Page, name: string) =>
  page.getByRole('columnheader', { name }).evaluate((el) => getComputedStyle(el).textAlign);

test('ledger headers are right-aligned over right-aligned columns; the lot title balances', async ({ browser, request }) => {
  test.setTimeout(120_000);
  const seller = await newAccount(request, 'seller', 'seller');
  const alice = await newAccount(request, 'alice');
  const bob = await newAccount(request, 'bob');
  const id = await newAuction(request, seller, { endInS: 600, reservePrice: null });
  await waitForStatus(request, id, 'live');
  await (await restBid(request, bob, id, 900000)).json();

  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  await signInUi(page, alice);
  await page.goto(`/auctions/${id}`);
  await expect(page.getByRole('columnheader', { name: enIN.roomUi.ledger.amount })).toBeVisible();

  expect(await align(page, enIN.roomUi.ledger.amount), 'Amount header').toBe('right');
  expect(await align(page, enIN.roomUi.ledger.decidedAt), 'Decided at header').toBe('right');
  expect(await align(page, enIN.roomUi.ledger.no), 'No header').toBe('left');

  const wrap = await page.getByRole('heading', { level: 1 }).evaluate((el) => getComputedStyle(el).textWrap || getComputedStyle(el).getPropertyValue('text-wrap'));
  expect(wrap).toBe('balance');
  await ctx.close();
});
