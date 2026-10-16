// BR-6, BR-7, FR-30 on the seller's screens: a reserve amount reaches only its own seller, a
// bidder's maximum reaches no seller at all, and a draft reaches nobody but its seller: not in
// the DOM, not in any JSON body the browser receives, not in the console.
import { expect, test, type Page } from '@playwright/test';

import { enIN } from '../src/strings/en-IN';
import { sellerEnIN as t } from '../src/strings/seller-en-IN';
import { newAccount, newAuction, restBid, restProxy, signInUi, waitForStatus } from './fixtures/stack';

const RESERVE = 1025000; // ₹10,250
const MAXIMUM = 1234567; // ₹12,345.67: not start + k×increment, so the proxy never bids at its limit
const RESERVE_SEEN = ['1025000', '10,250'];
const MAXIMUM_SEEN = ['1234567', '12,345'];
const tag = () => Math.random().toString(36).slice(2, 8);

/** Everything the page receives and shows, collected for a scan. */
function record(page: Page): string[] {
  const seen: string[] = [];
  page.on('console', (m) => seen.push(`console ${m.text()}`));
  page.on('response', async (r) => {
    if ((r.headers()['content-type'] ?? '').includes('application/json')) {
      try {
        seen.push(`body ${r.url()} ${await r.text()}`);
      } catch {
        /* a body that cannot be read carries nothing */
      }
    }
  });
  return seen;
}

const found = (seen: string[], secrets: string[]) => seen.filter((line) => secrets.some((s) => line.includes(s)));

test('another seller gets neither the reserve, a maximum, nor the draft; the seller sees the own reserve and never a maximum', async ({ browser, request }) => {
  test.setTimeout(150_000);
  const title = `Contax ${tag()} G2`;
  const seller = await newAccount(request, 'seller', 'seller');
  const rival = await newAccount(request, 'rival', 'seller');
  const alice = await newAccount(request, 'alice');
  const bob = await newAccount(request, 'bob');
  const id = await newAuction(request, seller, { title, startInS: 2, endInS: 600, reservePrice: RESERVE });
  const draft = await newAuction(request, seller, { title: `${title} draft`, publish: false, startInS: 3600, endInS: 7200, reservePrice: RESERVE });
  await waitForStatus(request, id, 'live');
  await (await restBid(request, bob, id, 900000)).json();
  await (await restProxy(request, alice, id, MAXIMUM)).json(); // answers 950000
  await (await restBid(request, bob, id, 1000000)).json(); // answers 1050000: reserve met

  // another seller: the public listing without a form, no draft, nothing of it on the dashboard
  {
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const page = await ctx.newPage();
    const seen = record(page);
    await signInUi(page, rival);
    await page.goto(`/sell/${id}`);
    await expect(page.getByText(t.page.notYours)).toBeVisible();
    await expect(page.getByLabel(t.form.title, { exact: true })).toHaveCount(0);
    seen.push(`dom ${await page.locator('body').innerText()}`);
    await page.goto(`/sell/${draft}`);
    await expect(page.getByText(enIN.errors.NOT_FOUND)).toBeVisible(); // FR-30
    await page.goto('/sell');
    await expect(page.getByText(t.dashboard.empty)).toBeVisible();
    seen.push(`dom ${await page.locator('body').innerText()}`);
    const secrets = [...RESERVE_SEEN, ...MAXIMUM_SEEN, `${title} draft`]; // the listing itself is public; its draft is not
    expect(found(seen, secrets), found(seen, secrets).slice(0, 3).join('\n')).toEqual([]);
    await ctx.close();
  }

  // the seller: the own reserve on the own listing, a bidder's maximum nowhere (BR-7, FR-51)
  {
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const page = await ctx.newPage();
    const seen = record(page);
    await signInUi(page, seller);
    await page.goto('/sell');
    await expect(page.getByRole('link', { name: title, exact: true })).toBeVisible();
    seen.push(`dom ${await page.locator('body').innerText()}`);
    expect(found(seen, RESERVE_SEEN), 'no reserve on the dashboard').toEqual([]);
    await page.goto(`/sell/${id}`);
    await expect(page.getByLabel(t.form.reserve, { exact: true })).toHaveValue('10,250');
    seen.push(`dom ${await page.locator('body').innerText()}`);
    expect(found(seen, MAXIMUM_SEEN), found(seen, MAXIMUM_SEEN).slice(0, 3).join('\n')).toEqual([]);
    await ctx.close();
  }
});
