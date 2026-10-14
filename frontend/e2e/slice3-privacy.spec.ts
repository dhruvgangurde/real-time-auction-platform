// BR-6 and BR-7 on the slice 3 screens: the reserve amount and a bidder's maximum never reach
// another viewer through Discover, the watchlist or notifications: not in the DOM, not in any
// JSON body the browser receives, not in the console. Bidders may learn only whether the reserve
// was met.
import { expect, test, type Page } from '@playwright/test';

import { newAccount, newAuction, restBid, restProxy, signInUi, waitForStatus } from './fixtures/stack';

const RESERVE = 1025000; // ₹10,250
const MAXIMUM = 1234567; // ₹12,345.67: not start + k×increment, so the proxy never bids at its limit
const SECRETS = ['1025000', '10,250', '1234567', '12,345'];
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

const leaks = (seen: string[]) => seen.filter((line) => SECRETS.some((s) => line.includes(s)));

test('Discover shows neither the reserve amount nor another bidder’s maximum, signed out or in', async ({ browser, request }) => {
  test.setTimeout(120_000);
  const t = tag();
  const seller = await newAccount(request, 'seller', 'seller');
  const alice = await newAccount(request, 'alice');
  const bob = await newAccount(request, 'bob');
  const id = await newAuction(request, seller, { title: `Contax ${t} G2`, startInS: 2, endInS: 600, reservePrice: RESERVE });
  await waitForStatus(request, id, 'live');
  await (await restBid(request, bob, id, 900000)).json();
  await (await restProxy(request, alice, id, MAXIMUM)).json(); // answers 950000
  await (await restBid(request, bob, id, 1000000)).json(); // answers 1050000: reserve met

  for (const who of [null, bob]) {
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const page = await ctx.newPage();
    const seen = record(page);
    if (who !== null) await signInUi(page, who);
    await page.goto(`/auctions?q=${t}`);
    await expect(page.locator('article')).toHaveCount(1);
    await expect(page.locator('article')).toContainText('₹10,500');
    seen.push(`dom ${await page.locator('body').innerText()}`);
    expect(leaks(seen), leaks(seen).slice(0, 3).join('\n')).toEqual([]);
    await ctx.close();
  }
});
