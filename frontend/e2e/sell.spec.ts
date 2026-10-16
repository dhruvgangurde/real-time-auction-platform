// The seller's screens on 8011 (brief 9.8; FR-11 to FR-19, FR-17, BR-4, BR-10, FR-64, FR-69,
// UI-7, UI-8, NFR-35): a draft from the form with its refused fields first, photos refused before
// and by the server, publish with the schedule, an edit while scheduled, the unsaved-changes
// warning, cancel; a live listing editable only in its text until the first bid, then read-only,
// then sold with the winner by name and the payment status; 390, dark and the keyboard.
import { expect, test, type Page } from '@playwright/test';

import { enIN } from '../src/strings/en-IN';
import { payEnIN } from '../src/strings/pay-en-IN';
import { sellerEnIN as t } from '../src/strings/seller-en-IN';
import { API, IMAGE, bearer, newAccount, newAuction, restBid, signInUi, waitForStatus } from './fixtures/stack';

const f = t.form;
const tag = () => Math.random().toString(36).slice(2, 7);
/** A datetime-local value in this machine's zone (the browser's too). */
const local = (ms: number) => {
  const d = new Date(ms);
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
};
const field = (page: Page, label: string) => page.getByLabel(label, { exact: true });
const status = (page: Page) => page.locator('h1').evaluate((h) => h.previousElementSibling?.textContent ?? '');

test('a draft from the form, refused fields first; photos refused before and by the server; publish, edit while scheduled, the unsaved warning, cancel', async ({ page, request }) => {
  test.setTimeout(150_000);
  const title = `Hasselblad ${tag()} 500C`;
  const seller = await newAccount(request, 'seller', 'seller');
  await signInUi(page, seller);
  await page.getByRole('link', { name: enIN.nav.sell, exact: true }).click();
  await expect(page.getByText(t.dashboard.empty)).toBeVisible();
  await page.getByRole('link', { name: t.dashboard.list }).click();

  // what the server would refuse is said under its field, the first one focused
  await field(page, f.title).fill(title);
  await field(page, f.description).fill('A body, an 80 mm Planar and an A12 back.');
  await field(page, f.category).selectOption({ index: 1 });
  await field(page, f.startingPrice).fill('9,000');
  await field(page, f.minIncrement).fill('500');
  await field(page, f.reserve).fill('8,000');
  await field(page, f.start).fill(local(Date.now() - 3_600_000));
  await field(page, f.end).fill(local(Date.now() + 7_200_000));
  await page.getByRole('button', { name: f.create }).click();
  await expect(page.getByText(t.problems.reserveBelowStart)).toBeVisible();
  await expect(page.getByText(t.problems.past)).toBeVisible();
  await expect(field(page, f.reserve)).toBeFocused();
  await expect(page.getByRole('alert').filter({ hasText: enIN.errors.VALIDATION_ERROR })).toBeVisible();
  await field(page, f.reserve).fill('10,250');
  await field(page, f.start).fill(local(Date.now() + 3_600_000));
  await page.getByRole('button', { name: f.create }).click();
  await page.waitForURL(/\/sell\/[0-9a-f-]{36}$/);
  const id = page.url().split('/').at(-1)!;
  expect(await status(page)).toBe(enIN.cards.status.draft);
  await expect(page.getByText(t.page.draftNote)).toBeVisible();

  // Publish with no photo: the reason here, the focus on the photos, and no request at all (FR-13)
  const publishes: string[] = [];
  page.on('request', (r) => r.url().endsWith('/publish') && publishes.push(r.url()));
  await page.getByRole('button', { name: t.actions.publish }).click();
  await expect(page.getByRole('alert').filter({ hasText: t.problems.images })).toBeVisible();
  await expect(page.getByRole('region', { name: t.photos.title })).toBeFocused();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  expect(publishes).toEqual([]);

  // photos: two refused here, one refused by the server (it decodes the file), one added as the cover
  await page.getByLabel(t.photos.add).setInputFiles([
    { name: 'big.jpg', mimeType: 'image/jpeg', buffer: Buffer.alloc(6 * 1024 * 1024) },
    { name: 'note.txt', mimeType: 'text/plain', buffer: Buffer.from('not an image') },
    { name: 'fake.jpg', mimeType: 'image/jpeg', buffer: Buffer.from('not a jpeg either') },
  ]);
  await expect(page.getByText(t.photos.failed('big.jpg', enIN.errors.IMAGE_TOO_LARGE))).toBeVisible();
  await expect(page.getByText(t.photos.failed('note.txt', enIN.errors.UNSUPPORTED_IMAGE))).toBeVisible();
  await expect(page.getByText(t.photos.failed('fake.jpg', enIN.errors.UNSUPPORTED_IMAGE))).toBeVisible({ timeout: 15_000 });
  await page.getByRole('button', { name: t.photos.dismissLabel('big.jpg') }).click();
  await expect(page.getByText(t.photos.failed('big.jpg', enIN.errors.IMAGE_TOO_LARGE))).toHaveCount(0);
  await page.getByLabel(t.photos.add).setInputFiles(IMAGE);
  await expect(page.getByAltText(t.photos.alt(1, 1))).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText(t.photos.coverTag, { exact: true })).toBeVisible();
  await expect(page.getByText(t.photos.cover)).toBeVisible(); // how to change the cover (Q6)

  // publish: the confirmation names it and gives the schedule in local time
  await page.getByRole('button', { name: t.actions.publish }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog.getByRole('heading', { name: t.actions.publishTitle(title) })).toBeVisible();
  await expect(dialog).toContainText(Intl.DateTimeFormat().resolvedOptions().timeZone);
  await dialog.getByRole('button', { name: t.actions.publish }).click();
  await expect(page.getByText(t.actions.published)).toBeVisible();
  expect(await status(page)).toBe(enIN.cards.status.scheduled);

  // scheduled: everything can still change; only the changed field is sent
  const patches: string[] = [];
  page.on('request', (r) => r.method() === 'PATCH' && patches.push(r.postData() ?? ''));
  await field(page, f.startingPrice).fill('9,500');
  await page.getByRole('button', { name: t.page.save }).click();
  await expect(page.getByText(t.page.saved)).toBeVisible();
  expect(patches.map((p) => JSON.parse(p) as object)).toEqual([{ starting_price: 950000 }]);
  const stored = (await (await request.get(`${API}/auctions/${id}`, { headers: bearer(seller) })).json()) as { start_price: number };
  expect(stored.start_price).toBe(950000);

  // leaving with an unsaved change asks first; Stay keeps it, Leave goes
  await field(page, f.title).fill(`${title} with a hood`);
  await page.getByRole('link', { name: t.page.back }).click();
  await expect(page.getByRole('heading', { name: f.leaveTitle })).toBeVisible();
  await page.getByRole('button', { name: f.stay }).click();
  await expect(field(page, f.title)).toHaveValue(`${title} with a hood`);
  await page.getByRole('link', { name: t.page.back }).click();
  await page.getByRole('button', { name: f.leave }).click();
  await page.waitForURL('**/sell');
  await expect(page.getByRole('region', { name: t.dashboard.sections.scheduled }).getByRole('link', { name: title })).toBeVisible();

  // cancel: named, then read-only, under Cancelled
  await page.getByRole('link', { name: title }).click();
  await page.getByRole('button', { name: t.actions.cancel }).click();
  await expect(page.getByRole('heading', { name: t.actions.cancelTitle(title) })).toBeVisible();
  await page.getByRole('dialog').getByRole('button', { name: t.actions.cancel }).click();
  await expect(page.getByText(t.actions.cancelled, { exact: true })).toBeVisible();
  expect(await status(page)).toBe(enIN.cards.status.cancelled);
  await expect(page.getByRole('button', { name: new RegExp(`${t.actions.publish}|${t.actions.cancel}|${t.page.save}`) })).toHaveCount(0);
  await page.getByRole('link', { name: t.page.back }).click();
  await expect(page.getByRole('region', { name: t.dashboard.sections.cancelled }).getByRole('link', { name: title })).toBeVisible();
});

test('live: only the text until the first bid, then read-only (BR-4); sold: the winner by name and the payment status (BR-10, FR-69)', async ({ page, request }) => {
  test.setTimeout(150_000);
  const seller = await newAccount(request, 'seller', 'seller');
  const buyer = await newAccount(request, 'buyer');
  const id = await newAuction(request, seller, { title: `Nikon ${tag()} F`, startInS: 2, endInS: 40, thresholdS: 10, extensionS: 10, reservePrice: null });
  await waitForStatus(request, id, 'live');
  await signInUi(page, seller);
  await page.goto(`/sell/${id}`);
  await expect(page.getByText(t.page.descriptive)).toBeVisible();
  await expect(field(page, f.title)).toBeEnabled();
  for (const label of [f.category, f.startingPrice, f.minIncrement, f.reserve, f.start, f.end, f.threshold]) await expect(field(page, label)).toBeDisabled();
  await expect(page.getByRole('button', { name: t.actions.publish })).toHaveCount(0);
  await expect(page.getByRole('button', { name: t.actions.cancel })).toHaveCount(0); // a seller cannot cancel once live (BR-9)
  const renamed = `Nikon F, black, ${tag()}`;
  await field(page, f.title).fill(renamed);
  await page.getByRole('button', { name: t.page.save }).click();
  await expect(page.getByText(t.page.saved)).toBeVisible();
  await expect(page.getByRole('heading', { level: 1 })).toHaveText(renamed);

  await (await restBid(request, buyer, id, 900000)).json();
  await page.reload();
  await expect(page.getByText(t.page.locked)).toBeVisible();
  await expect(field(page, f.title)).toBeDisabled();
  await expect(page.getByRole('button', { name: t.page.save })).toHaveCount(0);

  await waitForStatus(request, id, 'closed_sold', 60_000);
  await page.reload();
  const result = page.getByRole('region', { name: t.closed.title });
  await expect(result).toContainText(payEnIN.statusWon(buyer.name, '₹9,000'));
  await expect(result).toContainText(payEnIN.statusAwaiting);
  await expect(result).not.toContainText(/attempt|did not go through|Sold for/i); // one line: who won at what price
});

test('at 390 in dark, from the keyboard: the dashboard, the form and the listing page fit; Tab reaches every field and Save; Enter submits and the first problem takes the focus', async ({ browser, request }) => {
  test.setTimeout(120_000);
  const seller = await newAccount(request, 'seller', 'seller');
  const draft = await newAuction(request, seller, { title: `Rollei ${tag()} 35`, publish: false, startInS: 3600, endInS: 7200 });
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, colorScheme: 'dark' });
  const page = await ctx.newPage();
  await signInUi(page, seller);
  const fits = async () => expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);

  await page.goto('/sell');
  await expect(page.getByRole('region', { name: t.dashboard.sections.draft })).toBeVisible();
  await expect(page.locator('body')).toHaveCSS('background-color', 'rgb(13, 20, 36)');
  await fits();

  await page.goto('/sell/new');
  await field(page, f.title).focus();
  const reached = new Set<string>();
  for (let i = 0; i < 60 && !reached.has('save'); i += 1) {
    const name = await page.evaluate((save) => {
      const el = document.activeElement as HTMLInputElement | null;
      return el?.name || (el?.textContent === save ? 'save' : '');
    }, f.create);
    if (name) reached.add(name);
    if (name !== 'save') await page.keyboard.press('Tab');
  }
  expect([...reached].sort()).toEqual(['categoryId', 'description', 'end', 'extension', 'minIncrement', 'reserve', 'save', 'start', 'startingPrice', 'threshold', 'title']);
  await page.keyboard.press('Enter');
  await expect(field(page, f.title)).toBeFocused();
  await expect(page.getByText(t.problems.required).first()).toBeVisible();
  await fits();

  await page.goto(`/sell/${draft}`);
  await expect(page.getByRole('button', { name: t.actions.publish })).toBeVisible();
  await fits();
  await ctx.close();
});

// the dashboard's height at 390 with these six listings as full-width cards, measured before the
// compact rows (2026-10-17): the rows must take at most half of it
const CARDS_AT_390 = 3080;

test('at 390 the dashboard lists compact rows, thumbnail beside the title, status, price and time: no sideways scroll, at most half the height of cards', async ({ browser, request }) => {
  test.setTimeout(90_000);
  const seller = await newAccount(request, 'seller', 'seller');
  const k = tag();
  for (const n of [1, 2]) await newAuction(request, seller, { title: `Draft ${k} ${n}`, publish: false, startInS: 3600, endInS: 7200 });
  for (const n of [1, 2, 3]) await newAuction(request, seller, { title: `Scheduled ${k} ${n}`, startInS: 3600, endInS: 7200 });
  const gone = await newAuction(request, seller, { title: `Cancelled ${k}`, startInS: 3600, endInS: 7200 });
  await request.post(`${API}/auctions/${gone}/cancel`, { headers: bearer(seller) });
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 } });
  const page = await ctx.newPage();
  await signInUi(page, seller);
  await page.goto('/sell');
  await expect(page.locator('article')).toHaveCount(6);
  const shape = await page.evaluate(() => {
    const row = document.querySelector('article')!;
    const [thumb, body] = [...row.children].map((c) => c.getBoundingClientRect());
    return {
      height: document.documentElement.scrollHeight,
      overflow: document.documentElement.scrollWidth - window.innerWidth,
      beside: thumb!.right <= body!.left && Math.abs(thumb!.top - body!.top) < 40, // the thumbnail beside the text, not above it
    };
  });
  console.log('dashboard at 390:', shape);
  expect(shape.overflow).toBeLessThanOrEqual(0);
  expect(shape.beside).toBe(true);
  expect(shape.height).toBeLessThanOrEqual(CARDS_AT_390 / 2);
  for (const what of [enIN.cards.status.draft, enIN.cards.startingBid, '₹9,000']) await expect(page.locator('article').first()).toContainText(what);
  await ctx.close();
});
