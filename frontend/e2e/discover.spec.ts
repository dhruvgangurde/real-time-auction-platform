// Discover and search (brief 9.2; FR-20 to FR-23, FR-28, FR-30, UI-4, UI-7, NFR-35) on 8011:
// the URL holds every filter, so back, forward, reload and a shared link restore the same list;
// a category includes its descendants; min above max is caught before any request; "Show more"
// pages; no socket is ever opened here; countdowns tick from the server clock and a live card
// says Closing at zero; phones (390) and keyboard work; the dark theme renders.
import { expect, test, type Page } from '@playwright/test';

import { enIN } from '../src/strings/en-IN';
import { API, bearer, newAccount, newAuction, restBid, signInUi, waitForStatus, type Account } from './fixtures/stack';

const d = enIN.discover;
const tag = () => Math.random().toString(36).slice(2, 8);

/** Count requests to the list endpoint, and fail on any console error or CSP violation. */
function watch(page: Page) {
  const listCalls: string[] = [];
  const errors: string[] = [];
  const sockets: string[] = [];
  page.on('request', (r) => {
    if (/\/api\/v1\/auctions\?/.test(r.url())) listCalls.push(r.url());
  });
  page.on('console', (m) => m.type() === 'error' && errors.push(m.text()));
  page.on('pageerror', (e) => errors.push(e.message));
  page.on('websocket', (ws) => sockets.push(ws.url()));
  return { listCalls, errors, sockets };
}

const cards = (page: Page) => page.locator('article');
const titles = async (page: Page) => (await cards(page).locator('h2').allInnerTexts()).sort();

test('search and filters live in the URL: back, forward, reload and a shared link restore them', async ({ browser, request }) => {
  test.setTimeout(120_000);
  const t = tag();
  const seller = await newAccount(request, 'seller', 'seller');
  const leica = await newAuction(request, seller, { title: `Leica ${t} M3 rangefinder`, startInS: 2, endInS: 900, reservePrice: null });
  await newAuction(request, seller, { title: `Nikon ${t} F2 body`, startInS: 3600, endInS: 7200, reservePrice: null, categoryId: 2 });
  await waitForStatus(request, leica, 'live'); // one live, one scheduled

  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  const seen = watch(page);
  await page.goto('/auctions');
  await expect(page.getByRole('heading', { level: 1, name: d.title })).toBeVisible();

  // search: one history entry; the default is live and closing, so the live lot only (FR-22)
  await page.getByLabel(d.searchLabel).fill(t);
  await page.getByLabel(d.searchLabel).press('Enter');
  await expect(page).toHaveURL(new RegExp(`/auctions\\?q=${t}$`));
  await expect(cards(page)).toHaveCount(1);
  await expect(cards(page).locator('h2')).toHaveText([`Leica ${t} M3 rangefinder`]);
  await expect(page.getByLabel(d.statusNames.live, { exact: true })).toBeChecked();
  await expect(page.getByLabel(d.statusNames.closing, { exact: true })).toBeChecked();
  await expect(page.getByLabel(d.statusNames.scheduled, { exact: true })).not.toBeChecked();

  // scheduled is an opt-in tick, composed with the search (FR-23)
  await page.getByLabel(d.statusNames.scheduled, { exact: true }).check();
  await page.getByRole('button', { name: d.apply }).click();
  await expect(page).toHaveURL(new RegExp(`q=${t}&status=scheduled&status=live&status=closing$`));
  await expect(cards(page)).toHaveCount(2);

  // a category includes its descendants (FR-21): Nikon is in phones, a child of electronics
  await page.getByLabel(d.category).selectOption('electronics');
  await page.getByRole('button', { name: d.apply }).click();
  await expect(page).toHaveURL(new RegExp(`q=${t}&category=electronics&status=scheduled&status=live&status=closing$`));
  await expect(cards(page)).toHaveCount(2);
  await page.getByLabel(d.category).selectOption('phones');
  await page.getByRole('button', { name: d.apply }).click();
  await expect(page).toHaveURL(new RegExp(`category=phones&status=scheduled&status=live&status=closing$`));
  await expect(cards(page)).toHaveCount(1);
  await expect(cards(page).locator('h2')).toHaveText([`Nikon ${t} F2 body`]);

  // scheduled alone
  await page.getByLabel(d.category).selectOption('');
  await page.getByLabel(d.statusNames.live, { exact: true }).uncheck();
  await page.getByLabel(d.statusNames.closing, { exact: true }).uncheck();
  await page.getByRole('button', { name: d.apply }).click();
  await expect(page).toHaveURL(new RegExp(`q=${t}&status=scheduled$`));
  await expect(cards(page)).toHaveCount(1);
  await expect(cards(page).first()).toContainText(enIN.cards.status.scheduled);

  // back and forward step through the searches and restore the form too
  await page.goBack();
  await expect(page).toHaveURL(/category=phones&status=scheduled&status=live&status=closing$/);
  await expect(cards(page)).toHaveCount(1);
  await expect(cards(page).locator('h2')).toHaveText([`Nikon ${t} F2 body`]);
  await expect(page.getByLabel(d.category)).toHaveValue('phones');
  await page.goBack();
  await page.goBack();
  await page.goBack();
  await expect(page).toHaveURL(new RegExp(`/auctions\\?q=${t}$`));
  await expect(cards(page)).toHaveCount(1);
  await expect(page.getByLabel(d.searchLabel)).toHaveValue(t);
  await expect(page.getByLabel(d.statusNames.scheduled, { exact: true })).not.toBeChecked();
  for (let i = 0; i < 4; i += 1) await page.goForward();
  await expect(page).toHaveURL(new RegExp(`q=${t}&status=scheduled$`));
  await expect(page.getByLabel(d.statusNames.scheduled, { exact: true })).toBeChecked();
  await expect(page.getByLabel(d.statusNames.live, { exact: true })).not.toBeChecked();

  // reload, and the same link opened elsewhere (a shared link)
  await page.reload();
  await expect(cards(page)).toHaveCount(1);
  const other = await browser.newPage();
  await other.goto(page.url());
  await expect(other.locator('article')).toHaveCount(1);
  expect(await titles(other)).toEqual(await titles(page));
  await other.close();

  // a shared link with a category, in a fresh browser (no cached tree): the select shows it too
  const fresh = await browser.newContext();
  const shared = await fresh.newPage();
  await shared.goto(`/auctions?q=${t}&category=phones&status=scheduled`);
  await expect(shared.locator('article')).toHaveCount(1);
  await expect(shared.getByLabel(d.category)).toHaveValue('phones');
  await fresh.close();

  // anonymous viewing is REST only (FR-28)
  expect(seen.sockets).toEqual([]);
  expect(seen.errors).toEqual([]);
  await ctx.close();
});

test('min above max is a field error with no request; unknown category, no match and an empty default say so', async ({ page }) => {
  const seen = watch(page);
  // the default view with nothing live (routed): the empty state offers the upcoming auctions
  const DEFAULT = '**/api/v1/auctions?status=live&status=closing&page=1&page_size=20';
  await page.route(DEFAULT, (r) =>
    r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ items: [], page: 1, page_size: 20, has_more: false, server_time_ms: Date.now() }) }),
  );
  await page.goto('/auctions');
  await expect(page.getByText(d.empty)).toBeVisible();
  await page.unroute(DEFAULT);
  await page.getByRole('link', { name: d.upcoming }).click();
  await expect(page).toHaveURL(/\/auctions\?status=scheduled$/);
  await expect(page.getByLabel(d.statusNames.scheduled, { exact: true })).toBeChecked();
  await expect(page.getByLabel(d.statusNames.live, { exact: true })).not.toBeChecked();

  await page.goto('/auctions');
  await expect(cards(page).or(page.getByText(d.empty))).not.toHaveCount(0);
  const before = seen.listCalls.length;
  await page.getByLabel(d.min).fill('5000');
  await page.getByLabel(d.max).fill('100');
  await page.getByRole('button', { name: d.apply }).click();
  await expect(page.getByText(d.range)).toBeVisible();
  await expect(page.getByLabel(d.max)).toHaveAttribute('aria-invalid', 'true');
  await expect(page).toHaveURL(/\/auctions$/);
  expect(seen.listCalls.length).toBe(before);

  // the same pair in a hand-edited link: the error, and still nothing asked
  const calls = seen.listCalls.length;
  await page.goto('/auctions?min=500&max=100');
  await expect(page.getByText(d.range)).toBeVisible();
  expect(seen.listCalls.length).toBe(calls);

  await page.goto('/auctions?category=no-such-category');
  await expect(page.getByText(d.unknownCategory)).toBeVisible();

  await page.goto(`/auctions?q=zz${tag()}qq`);
  await expect(page.getByText(d.noMatch)).toBeVisible();
  await expect(page.getByText(d.filteredBy)).toBeVisible();
  await page.getByRole('link', { name: d.clear }).first().click();
  await expect(page).toHaveURL(/\/auctions$/);
  expect(seen.errors).toEqual([]);
});

test('Show more pages through the results, keeps them on reload and on the way back from a room', async ({ page, request }) => {
  test.setTimeout(180_000);
  const t = tag();
  const sellers: Account[] = [];
  for (let i = 0; i < 3; i += 1) sellers.push(await newAccount(request, 'seller', 'seller')); // 10 listings an hour each (NFR-22)
  for (let i = 0; i < 21; i += 1) {
    await newAuction(request, sellers[i % 3]!, { title: `Lot ${t} number ${i}`, startInS: 3600, endInS: 7200, reservePrice: null });
  }
  await page.goto(`/auctions?q=${t}&status=scheduled`);
  await expect(cards(page)).toHaveCount(20);
  await page.getByRole('button', { name: d.showMore }).click();
  await expect(page).toHaveURL(new RegExp(`q=${t}&status=scheduled&page=2$`));
  await expect(cards(page)).toHaveCount(21);
  await expect(page.locator('a[data-card]').nth(20)).toBeFocused(); // focus on the first new card
  await expect(page.getByRole('button', { name: d.showMore })).toHaveCount(0);

  await page.reload();
  await expect(cards(page)).toHaveCount(21);
  await page.locator('a[data-card]').nth(20).click();
  await expect(page).toHaveURL(/\/auctions\/[0-9a-f-]{36}$/);
  await page.goBack();
  await expect(page).toHaveURL(new RegExp(`page=2$`));
  await expect(cards(page)).toHaveCount(21);
});

test('signed in, still no socket; a live countdown ticks from the server clock and says Closing at zero', async ({ browser, request }) => {
  test.setTimeout(120_000);
  const t = tag();
  const seller = await newAccount(request, 'seller', 'seller');
  const viewer = await newAccount(request, 'viewer');
  const id = await newAuction(request, seller, { title: `Rolleiflex ${t}`, startInS: 2, endInS: 40, thresholdS: 10, extensionS: 10, reservePrice: null });
  await waitForStatus(request, id, 'live');

  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  const seen = watch(page);
  await page.goto('/sign-in');
  await page.getByLabel('Email').fill(viewer.email);
  await page.getByLabel('Password').fill(viewer.password);
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await page.waitForURL('http://localhost:8011/');
  await page.goto(`/auctions?q=${t}`);
  const card = cards(page).first();
  const line = card.getByText(/^Ends in /);
  await expect(line).toBeVisible();
  const first = await line.innerText();
  await expect(line).not.toHaveText(first, { timeout: 3_000 }); // the one page tick repaints it

  // at zero the card says Closing without a reload (G3), then the list is asked again
  await expect(card).toContainText(enIN.cards.status.closing, { timeout: 45_000 });
  await expect(card).toContainText(enIN.cards.closingNote);
  expect(seen.sockets).toEqual([]);
  expect(seen.errors).toEqual([]);
  await ctx.close();
});

test('at 390 the filters open from a button, the page never scrolls sideways, and it all works from the keyboard', async ({ browser, request }) => {
  const t = tag();
  const seller = await newAccount(request, 'seller', 'seller');
  await newAuction(request, seller, { title: `Hasselblad ${t} 500C`, startInS: 3600, endInS: 7200, reservePrice: null });
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 } });
  const page = await ctx.newPage();
  await page.goto('/auctions');
  await expect(page.getByLabel(d.category)).toBeHidden();

  // keyboard only: search (a scheduled lot is not in the default), open the filters, tick Scheduled, apply
  await page.getByLabel(d.searchLabel).focus();
  await page.keyboard.type(t);
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(new RegExp(`q=${t}$`));
  await expect(page.getByText(d.noMatch)).toBeVisible();
  const toggle = page.getByRole('button', { name: d.filtersToggle(0) });
  await toggle.focus();
  await page.keyboard.press('Enter');
  await expect(toggle).toHaveAttribute('aria-expanded', 'true');
  await expect(page.getByLabel(d.category)).toBeVisible();
  await page.getByLabel(d.statusNames.scheduled, { exact: true }).focus();
  await page.keyboard.press('Space');
  await page.getByRole('button', { name: d.apply }).focus();
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(new RegExp(`q=${t}&status=scheduled&status=live&status=closing$`));
  await expect(cards(page)).toHaveCount(1);
  await expect(page.getByRole('button', { name: d.filtersToggle(1) })).toBeVisible();

  // a card is one link, reachable with Tab, with a visible focus ring around the card
  await page.locator('a[data-card]').first().focus();
  await expect(page.locator('article').first()).toHaveCSS('outline-style', 'solid');
  expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);
  await ctx.close();
});

// frame 04's flow: finished lots (sold, not sold) offer no Watch; one already watched shows
// Watching, so it can be removed, and then the button goes
test('finished lots show no Watch unless watched; a watched one can be removed', async ({ page, request }) => {
  test.setTimeout(120_000);
  const t = tag();
  const seller = await newAccount(request, 'seller', 'seller');
  const bidder = await newAccount(request, 'bidder');
  const viewer = await newAccount(request, 'viewer');
  const short = { startInS: 2, endInS: 25, thresholdS: 10, extensionS: 10, reservePrice: null } as const;
  const sold = await newAuction(request, seller, { ...short, title: `Kodak ${t} Retina sold` });
  const unsold = await newAuction(request, seller, { ...short, title: `Kodak ${t} Retina unsold` });
  await waitForStatus(request, sold, 'live');
  await (await restBid(request, bidder, sold, 900000)).json();
  await request.put(`${API}/watchlist/${sold}`, { headers: bearer(viewer) }); // watched while live
  await waitForStatus(request, sold, 'closed_sold', 60_000);
  await waitForStatus(request, unsold, 'closed_no_sale', 60_000);

  await signInUi(page, viewer);
  await page.goto(`/auctions?q=${t}&status=closed_sold&status=closed_no_sale`);
  const soldCard = cards(page).filter({ hasText: `Kodak ${t} Retina sold` });
  const unsoldCard = cards(page).filter({ hasText: `Kodak ${t} Retina unsold` });
  await expect(soldCard.getByRole('button', { name: enIN.watch.watching })).toHaveAttribute('aria-pressed', 'true');
  await expect(unsoldCard).toContainText(enIN.cards.status.noSale);
  await expect(unsoldCard.getByRole('button')).toHaveCount(0);

  await soldCard.getByRole('button', { name: enIN.watch.watching }).click();
  await expect(soldCard.getByRole('button')).toHaveCount(0); // removed, and nothing to watch again
  await expect.poll(async () => ((await (await request.get(`${API}/watchlist`, { headers: bearer(viewer) })).json()) as { items: unknown[] }).items.length).toBe(0);
});

test('the dark theme renders with its own paper and ink', async ({ browser }) => {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: 'dark' });
  const page = await ctx.newPage();
  const seen = watch(page);
  await page.goto('/auctions');
  await expect(page.getByRole('heading', { level: 1, name: d.title })).toBeVisible();
  await expect(page.locator('body')).toHaveCSS('background-color', 'rgb(13, 20, 36)'); // --paper, dark
  await expect(page.locator('body')).toHaveCSS('color', 'rgb(233, 237, 244)'); // --ink, dark
  expect(seen.errors).toEqual([]);
  await ctx.close();
});
