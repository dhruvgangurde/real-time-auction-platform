// BR-6, BR-7 and FR-28 on the landing (brief 8; slice 5): a reserve amount and a bidder's maximum
// never reach the page, not in any JSON body, the DOM or the console, signed out or signed in as
// the maximum's owner (every landing request is anonymous); the featured lots are real listings
// (is_demo false on every item); the replay shows bids by a maximum, never the maximum; and the
// page opens no socket.
import { expect, request, test, type Page } from '@playwright/test';

import { landingEnIN as l } from '../src/strings/landing-en-IN';
import { MAXIMUM, overview, replayLot, RESERVE, routeDemo, SECRETS, type ReplayLot } from './fixtures/landing';
import { newAccount, newAuction, restBid, restProxy, signInUi, waitForStatus, type Account } from './fixtures/stack';

let replay: ReplayLot;
let owner: Account;
let featuredTitle: string;

test.beforeAll(async () => {
  test.setTimeout(150_000);
  const api = await request.newContext();
  const t = Math.random().toString(36).slice(2, 8);
  replay = await replayLot(api, `Hasselblad ${t} 500C/M replay`);
  // a live featured lot with a reserve and a maximum on it
  const seller = await newAccount(api, 'seller', 'seller');
  owner = await newAccount(api, 'alice');
  const bob = await newAccount(api, 'bob');
  featuredTitle = `Hasselblad ${t} 500C/M live`;
  const id = await newAuction(api, seller, { title: featuredTitle, startInS: 2, endInS: 1800, reservePrice: RESERVE });
  await waitForStatus(api, id, 'live');
  await (await restBid(api, bob, id, 900000)).json();
  await (await restProxy(api, owner, id, MAXIMUM)).json(); // answers 950000
  await (await restBid(api, bob, id, 1000000)).json(); // answers 1050000
  await api.dispose();
});

/** Everything the page receives and shows, the list items, and any socket. */
function record(page: Page) {
  const seen: string[] = [];
  const items: { is_demo: unknown }[] = [];
  const sockets: string[] = [];
  page.on('console', (m) => seen.push(`console ${m.text()}`));
  page.on('websocket', (ws) => sockets.push(ws.url()));
  page.on('response', async (r) => {
    if (!(r.headers()['content-type'] ?? '').includes('application/json')) return;
    try {
      const body = await r.text();
      seen.push(`body ${r.url()} ${body}`);
      if (/\/api\/v1\/auctions\?/.test(r.url())) items.push(...(JSON.parse(body) as { items: { is_demo: unknown }[] }).items);
    } catch {
      /* a body that cannot be read carries nothing */
    }
  });
  return { seen, items, sockets };
}

const leaks = (seen: string[]) => seen.filter((line) => SECRETS.some((s) => line.includes(s)));

for (const who of ['signed out', 'the maximum’s owner'] as const) {
  test(`the landing shows neither the reserve nor a maximum, ${who}`, async ({ page }) => {
    test.setTimeout(90_000);
    if (who !== 'signed out') await signInUi(page, owner);
    await routeDemo(page, { overview: () => overview(replay.id) });
    const run = record(page);
    await page.goto('/');

    await expect(page.getByRole('region', { name: l.featured.title }).getByRole('link', { name: featuredTitle })).toBeVisible();
    const board = page.getByRole('region', { name: l.board.label });
    await board.scrollIntoViewIfNeeded(); // it plays once half in view (slice 5 Q2)
    await expect(board.getByRole('button', { name: l.board.again })).toBeVisible({ timeout: 45_000 });
    await expect(board.locator('tbody td').getByText(l.board.ledger.byMaximum, { exact: true })).toHaveCount(2); // that it was a maximum, not its amount

    run.seen.push(`dom ${await page.locator('body').innerText()}`);
    expect(leaks(run.seen), leaks(run.seen).slice(0, 3).join('\n')).toEqual([]);
    expect(run.items.length).toBeGreaterThan(0);
    expect(run.items.every((i) => i.is_demo === false)).toBe(true);
    expect(run.sockets).toEqual([]);
  });
}
