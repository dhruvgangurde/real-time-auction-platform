// The administrator's overview (FR-81, gap A1; NFR-8) on 8011: health, the counters moving after a
// real bid and a real socket, the newest audit entries, and a user search that opens Users. Only
// an administrator can read it: a bidder gets 403 from the API and the denied page in place.
import { expect, test, type Page } from '@playwright/test';

import { adminEnIN } from '../src/strings/admin-en-IN';
import { enIN } from '../src/strings/en-IN';
import { API, bearer, newAccount, newAuction, restBid, signInUi, waitForLive, waitForStatus } from './fixtures/stack';

const t = adminEnIN.overview;
const accepted = async (page: Page) => Number(/^(\d+) accepted/.exec(await page.getByText(/ accepted, at most /).innerText())?.[1]);
const sockets = async (page: Page) =>
  (await page.getByText(/^api(-2)?: \d+ connection/).allInnerTexts()).reduce((n, line) => n + Number(/: (\d+) /.exec(line)?.[1] ?? 0), 0);

test('the overview: health, counters that follow a real bid and socket, recent audit, user search', async ({ browser, request }) => {
  test.setTimeout(120_000);
  const admin = await newAccount(request, 'admin', 'admin');
  const seller = await newAccount(request, 'seller', 'seller');
  const bidder = await newAccount(request, 'bidder');
  const id = await newAuction(request, seller, { startInS: 2, endInS: 600, reservePrice: null });
  await waitForStatus(request, id, 'live');

  const page = await (await browser.newContext({ viewport: { width: 1440, height: 900 } })).newPage();
  await signInUi(page, admin);
  await page.goto('/admin');
  await expect(page.getByRole('heading', { name: t.title, level: 2 })).toBeVisible();
  await expect(page.getByRole('link', { name: adminEnIN.area.overview })).toHaveAttribute('aria-current', 'page');
  await expect(page.getByText(t.ok, { exact: true })).toHaveCount(3); // Postgres, Redis, the broker
  await expect(page.getByText(/^api: \d+ connection/)).toBeVisible();
  await expect(page.getByText(/^api-2: \d+ connection/)).toBeVisible();
  const before = await accepted(page);
  const socketsBefore = await sockets(page);

  // a real bid, and a real socket in another browser
  expect((await restBid(request, bidder, id, 900000)).status()).toBe(200);
  const room = await (await browser.newContext()).newPage();
  await signInUi(room, bidder);
  await room.goto(`/auctions/${id}`);
  await waitForLive(room);
  await expect.poll(() => accepted(page), { timeout: 15_000 }).toBeGreaterThanOrEqual(before + 1); // read again every 5 s
  await expect.poll(() => sockets(page), { timeout: 20_000 }).toBeGreaterThan(socketsBefore);

  // the newest audit entries, and the way to all of them
  await expect(page.getByRole('heading', { name: t.recent })).toBeVisible();
  await expect(page.getByRole('link', { name: t.allAudit })).toHaveAttribute('href', '/admin/audit');

  // find a user: Users opens with the search filled
  await page.getByLabel(t.searchLabel).fill(bidder.email);
  await page.getByRole('button', { name: t.searchButton }).click();
  await expect(page).toHaveURL(`/admin/users?q=${encodeURIComponent(bidder.email)}`);
  await expect(page.getByText(bidder.email)).toBeVisible();
});

test('only an administrator can read the overview', async ({ page, request }) => {
  const bidder = await newAccount(request, 'bidder');
  expect((await request.get(`${API}/admin/overview`)).status()).toBe(401);
  expect((await request.get(`${API}/admin/overview`, { headers: bearer(bidder) })).status()).toBe(403);
  await signInUi(page, bidder);
  await page.goto('/admin');
  await expect(page.getByRole('heading', { name: enIN.guards.deniedTitle })).toBeVisible();
});
