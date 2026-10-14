// The watchlist and the Watch toggle (brief 9.7; FR-24, FR-25, UI-2, UI-7, NFR-35) on 8011: a
// signed-out Watch goes to sign-in and back to the same URL without adding anything (Q5); watch
// is optimistic and persists; a refusal rolls back with a message; the page lists every status,
// keeps a removed item with "Watch again" until the viewer leaves; empty, 390, keyboard, dark.
import { expect, test, type APIRequestContext, type Page } from '@playwright/test';

import { enIN } from '../src/strings/en-IN';
import { API, bearer, newAccount, newAuction, signInUi, waitForStatus, type Account } from './fixtures/stack';

const w = enIN.watch;
const tag = () => Math.random().toString(36).slice(2, 8);

async function watchedIds(api: APIRequestContext, who: Account): Promise<string[]> {
  const r = await api.get(`${API}/watchlist`, { headers: bearer(who) });
  return ((await r.json()) as { items: { id: string }[] }).items.map((i) => i.id);
}

function errorsOf(page: Page): string[] {
  const errors: string[] = [];
  page.on('console', (m) => m.type() === 'error' && errors.push(m.text()));
  page.on('pageerror', (e) => errors.push(e.message));
  return errors;
}

const watchButton = (page: Page) => page.getByRole('button', { name: new RegExp(`^(${w.watch}|${w.watching}|${w.again})$`) });

test('signed out, Watch goes to sign-in and back to the same URL, and adds nothing (Q5)', async ({ page, request }) => {
  const t = tag();
  const seller = await newAccount(request, 'seller', 'seller');
  const viewer = await newAccount(request, 'viewer');
  await newAuction(request, seller, { title: `Mamiya ${t} RB67`, startInS: 3600, endInS: 7200, reservePrice: null });
  await page.goto(`/auctions?q=${t}&status=scheduled`);
  await page.getByRole('button', { name: w.watch }).click();
  await page.waitForURL(/\/sign-in$/);
  await page.getByLabel('Email').fill(viewer.email);
  await page.getByLabel('Password').fill(viewer.password);
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await page.waitForURL(new RegExp(`/auctions\\?q=${t}&status=scheduled$`));
  await expect(page.getByRole('button', { name: w.watch })).toHaveAttribute('aria-pressed', 'false');
  expect(await watchedIds(request, viewer)).toEqual([]);
});

test('watch from a card and from the room; the page lists every status; remove keeps Watch again until leaving', async ({ page, request }) => {
  test.setTimeout(120_000);
  const t = tag();
  const seller = await newAccount(request, 'seller', 'seller');
  const viewer = await newAccount(request, 'viewer');
  const live = await newAuction(request, seller, { title: `Linhof ${t} live`, startInS: 2, endInS: 900, reservePrice: null });
  const later = await newAuction(request, seller, { title: `Linhof ${t} later`, startInS: 3600, endInS: 7200, reservePrice: null });
  const gone = await newAuction(request, seller, { title: `Linhof ${t} gone`, startInS: 3600, endInS: 3900, reservePrice: null });
  await waitForStatus(request, live, 'live');
  const errors = errorsOf(page);
  await signInUi(page, viewer);

  // a card: pressed at once (optimistic), and still pressed after a reload (persisted)
  await page.goto(`/auctions?q=${t}`);
  await expect(page.locator('article')).toHaveCount(3);
  const card = page.locator('article', { hasText: `Linhof ${t} later` });
  await card.getByRole('button', { name: w.watch }).click();
  await expect(card.getByRole('button', { name: w.watching })).toHaveAttribute('aria-pressed', 'true');
  await page.reload();
  await expect(page.locator('article', { hasText: `Linhof ${t} later` }).getByRole('button', { name: w.watching })).toBeVisible();

  // the room's lot column, with the ending-soon note (FR-73)
  await page.goto(`/auctions/${live}`);
  await expect(page.getByText(w.note)).toBeVisible();
  await page.getByRole('button', { name: w.watch }).click();
  await expect(page.getByRole('button', { name: w.watching })).toHaveAttribute('aria-pressed', 'true');
  await page.goto(`/auctions/${gone}`);
  await page.getByRole('button', { name: w.watch }).click();
  await expect(page.getByRole('button', { name: w.watching })).toBeVisible();
  await expect.poll(async () => (await watchedIds(request, viewer)).sort()).toEqual([gone, later, live].sort());

  // the seller withdraws one: it stays on the list, cancelled (every status, brief 9.7)
  await request.post(`${API}/auctions/${gone}/cancel`, { headers: bearer(seller) });
  await page.goto('/watchlist');
  await expect(page.getByRole('heading', { level: 1, name: enIN.watchlist.title })).toBeVisible();
  await expect(page.locator('article')).toHaveCount(3);
  await expect(page.locator('article').first()).toContainText(`Linhof ${t} live`); // ending soonest first
  await expect(page.locator('article', { hasText: `Linhof ${t} gone` })).toContainText(enIN.cards.status.cancelled);
  await expect(page.locator('article', { hasText: `Linhof ${t} live` })).toContainText(/Ends in \d+:\d\d/);

  // remove: the item stays, unpressed, with Watch again; Watch again undoes it
  const later2 = page.locator('article', { hasText: `Linhof ${t} later` });
  await later2.getByRole('button', { name: w.watching }).click();
  await expect(later2.getByRole('button', { name: w.again })).toHaveAttribute('aria-pressed', 'false');
  await expect.poll(async () => (await watchedIds(request, viewer)).includes(later)).toBe(false);
  await later2.getByRole('button', { name: w.again }).click();
  await expect(later2.getByRole('button', { name: w.watching })).toBeVisible();
  await expect.poll(async () => (await watchedIds(request, viewer)).includes(later)).toBe(true);

  // a cancelled lot can be removed but not watched again (ALREADY_CLOSED): no Watch again
  const gone2 = page.locator('article', { hasText: `Linhof ${t} gone` });
  await gone2.getByRole('button', { name: w.watching }).click();
  await expect(gone2.getByRole('button')).toHaveCount(0);
  await expect.poll(async () => (await watchedIds(request, viewer)).includes(gone)).toBe(false);

  // removed for good once the viewer leaves and comes back
  await page.goto('/auctions');
  await page.goto('/watchlist');
  await expect(page.locator('article')).toHaveCount(2);
  expect(errors).toEqual([]);
});

test('a refused watch rolls back with a message: a server error, and an auction cancelled meanwhile', async ({ page, request }) => {
  const t = tag();
  const seller = await newAccount(request, 'seller', 'seller');
  const viewer = await newAccount(request, 'viewer');
  const id = await newAuction(request, seller, { title: `Pentacon ${t} six`, startInS: 3600, endInS: 7200, reservePrice: null });
  await signInUi(page, viewer);
  await page.goto(`/auctions?q=${t}`);
  const button = () => page.locator('article').getByRole('button', { name: new RegExp(`^(${w.watch}|${w.watching})$`) });

  await page.route('**/api/v1/watchlist/*', (r) => r.fulfill({ status: 503, contentType: 'application/json', body: '{"error":{"code":"SERVICE_UNAVAILABLE","message":"down"}}' }));
  await button().click();
  await expect(page.getByText(w.addFailed)).toBeVisible();
  await expect(button()).toHaveAttribute('aria-pressed', 'false');
  await page.unroute('**/api/v1/watchlist/*');

  // the card was loaded while scheduled; the seller withdraws it before the click
  await request.post(`${API}/auctions/${id}/cancel`, { headers: bearer(seller) });
  await button().click();
  await expect(page.getByText(w.cancelled)).toBeVisible();
  await expect(button()).toHaveAttribute('aria-pressed', 'false');
  expect(await watchedIds(request, viewer)).toEqual([]);
});

test('an empty watchlist offers Discover; 390 and keyboard; the dark theme', async ({ browser, request }) => {
  const t = tag();
  const seller = await newAccount(request, 'seller', 'seller');
  const viewer = await newAccount(request, 'viewer');
  await newAuction(request, seller, { title: `Graflex ${t} Crown`, startInS: 3600, endInS: 7200, reservePrice: null });
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, colorScheme: 'dark' });
  const page = await ctx.newPage();
  const errors = errorsOf(page);
  await signInUi(page, viewer);
  await page.goto('/watchlist');
  await expect(page.getByText(enIN.watchlist.empty)).toBeVisible();
  await expect(page.locator('body')).toHaveCSS('background-color', 'rgb(13, 20, 36)');
  await page.getByRole('link', { name: enIN.watchlist.find }).click();
  await expect(page).toHaveURL(/\/auctions$/);

  // keyboard: the toggle takes focus and Space presses it
  await page.goto(`/auctions?q=${t}`);
  await watchButton(page).focus();
  await page.keyboard.press('Space');
  await expect(watchButton(page)).toHaveAttribute('aria-pressed', 'true');
  await expect(watchButton(page)).toBeFocused();
  await page.goto('/watchlist');
  await expect(page.locator('article')).toHaveCount(1);
  expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);
  expect(errors).toEqual([]);
  await ctx.close();
});
