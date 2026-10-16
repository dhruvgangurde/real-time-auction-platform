// Screenshots of slice 4a as built (the seller's screens and the paid room) for review, written to
// design/shots/06-slice4 with an index.md. Every frame is a real state driven through the API and
// the UI on 8011; the routed one says so in its note. The paid frames need a real sandbox order,
// so they are left out (and the index says so) when the api container cannot reach Razorpay.
// design/ is kept local (.gitignore); this spec regenerates the frames and the index.
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { expect, test, type APIRequestContext, type Page } from '@playwright/test';

import { accountEnIN } from '../src/strings/account-en-IN';
import { adminEnIN } from '../src/strings/admin-en-IN';
import { enIN } from '../src/strings/en-IN';
import { sellerEnIN as t } from '../src/strings/seller-en-IN';
import { apiReachesRazorpay, newPaymentId, sendWebhook } from './fixtures/pay';
import { API, IMAGE, bearer, grantRole, newAccount, newAuction, restBid, restProxy, signInUi, waitForStatus, type Account } from './fixtures/stack';

const OUT = fileURLToPath(new URL('../../design/shots/06-slice4', import.meta.url));
mkdirSync(OUT, { recursive: true });

type Row = { state: string; file: string; note?: string };
const rows: Row[] = [];
const notShot: string[] = [];
const tag = () => Math.random().toString(36).slice(2, 7);
const f = t.form;
const local = (ms: number) => {
  const d = new Date(ms);
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
};

/** 1440 then 390 (light, or dark), full page unless `viewport`. */
async function shoot(page: Page, state: string, theme: 'light' | 'dark', opts: { note?: string; viewport?: boolean } = {}) {
  for (const width of [1440, 390] as const) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 900 });
    await page.waitForTimeout(250);
    const file = `${state}-${width}-${theme}.png`;
    await page.screenshot({ path: `${OUT}/${file}`, fullPage: !opts.viewport });
    rows.push({ state, file, ...(opts.note ? { note: opts.note } : {}) });
  }
  await page.setViewportSize({ width: 1440, height: 900 });
}

async function paid(request: APIRequestContext, winner: Account, id: string) {
  const order = (await (await request.post(`${API}/auctions/${id}/payment`, { headers: bearer(winner) })).json()) as { order_id: string };
  await sendWebhook(request, 'payment.captured', { id: newPaymentId(), order_id: order.order_id, amount: 950000 });
  await expect
    .poll(async () => ((await (await request.get(`${API}/auctions/${id}/settlement`, { headers: bearer(winner) })).json()) as { status: string }).status)
    .toBe('paid');
}

test('Seller: dashboard, the form and its errors, photos, publish, every status, cancel, not yours; the paid room', async ({ browser, request }) => {
  test.setTimeout(300_000);
  const gateway = apiReachesRazorpay();
  const seller = await newAccount(request, 'seller', 'seller');
  const rival = await newAccount(request, 'rival', 'seller');
  const buyer = await newAccount(request, 'buyer');
  const k = tag();
  const close = { startInS: 2, endInS: 30, thresholdS: 10, extensionS: 10 };

  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  await signInUi(page, seller);
  await page.goto('/sell');
  await expect(page.getByText(t.dashboard.empty)).toBeVisible();
  await shoot(page, '01-sell-dashboard-empty', 'light');

  // one listing in every state, through the API, while the form frames are taken
  const sold = await newAuction(request, seller, { ...close, title: `Pentax ${k} K1000`, reservePrice: null });
  const soldPaid = await newAuction(request, seller, { ...close, title: `Leica ${k} M3`, reservePrice: null });
  const unsold = await newAuction(request, seller, { ...close, title: `Minolta ${k} X-700` });
  const liveFree = await newAuction(request, seller, { title: `Nikon ${k} F`, startInS: 2, endInS: 1800 });
  const liveBid = await newAuction(request, seller, { title: `Canon ${k} AE-1`, startInS: 2, endInS: 1800 });
  const scheduled = await newAuction(request, seller, { title: `Contax ${k} G2`, startInS: 3600, endInS: 7200 });
  const cancelled = await newAuction(request, seller, { title: `Yashica ${k} Mat`, startInS: 3600, endInS: 7200 });
  await request.post(`${API}/auctions/${cancelled}/cancel`, { headers: bearer(seller) });
  await newAuction(request, seller, { title: `Rolleiflex ${k} 2.8F`, publish: false, startInS: 3600, endInS: 7200 });
  for (const id of [sold, soldPaid, unsold, liveFree, liveBid]) await waitForStatus(request, id, 'live');
  for (const id of [sold, soldPaid]) await (await restBid(request, buyer, id, 950000)).json();
  await (await restBid(request, buyer, unsold, 900000)).json(); // below the ₹10,250 reserve
  await (await restBid(request, buyer, liveBid, 900000)).json();

  // the form: empty, then refused fields with the first focused
  await page.goto('/sell/new');
  await expect(page.getByRole('button', { name: f.create })).toBeVisible();
  await shoot(page, '03-sell-new', 'light');
  await page.getByLabel(f.title, { exact: true }).fill(`Hasselblad ${k} 500C`);
  await page.getByLabel(f.startingPrice, { exact: true }).fill('9,000');
  await page.getByLabel(f.reserve, { exact: true }).fill('8,000');
  await page.getByLabel(f.start, { exact: true }).fill(local(Date.now() - 3_600_000));
  await page.getByRole('button', { name: f.create }).click();
  await expect(page.getByText(t.problems.reserveBelowStart)).toBeVisible();
  await shoot(page, '04-sell-new-errors', 'light', { note: 'refused here, before sending: each problem under its field, the first focused' });
  await page.getByRole('button', { name: enIN.help.topics.reserve.question }).click();
  await expect(page.getByRole('dialog').getByRole('heading', { name: enIN.help.topics.reserve.title })).toBeVisible();
  await shoot(page, '05-sell-form-help', 'light', { note: 'the reserve field’s help topic in the drawer', viewport: true });
  await page.keyboard.press('Escape');
  await page.getByLabel(f.description, { exact: true }).fill('A body, an 80 mm Planar and an A12 back.');
  await page.getByLabel(f.category, { exact: true }).selectOption({ index: 1 });
  await page.getByLabel(f.minIncrement, { exact: true }).fill('500');
  await page.getByLabel(f.reserve, { exact: true }).fill('10,250');
  await page.getByLabel(f.start, { exact: true }).fill(local(Date.now() + 3_600_000));
  await page.getByLabel(f.end, { exact: true }).fill(local(Date.now() + 7_200_000));
  await page.getByRole('button', { name: f.create }).click();
  await page.waitForURL(/\/sell\/[0-9a-f-]{36}$/);
  await expect(page.getByText(t.page.draftNote)).toBeVisible();
  await shoot(page, '06-sell-draft', 'light', { note: 'just created: no photo yet' });

  // photos: one uploading (the upload routed to wait 4 s), two refused, then the cover
  await page.route('**/api/v1/auctions/*/images', async (route) => {
    await new Promise((r) => setTimeout(r, 4_000));
    await route.continue().catch(() => undefined);
  });
  await page.getByLabel(t.photos.add).setInputFiles([
    { name: 'test-lot.jpg', mimeType: 'image/jpeg', buffer: readFileSync(IMAGE) },
    { name: 'big.jpg', mimeType: 'image/jpeg', buffer: Buffer.alloc(6 * 1024 * 1024) },
    { name: 'note.txt', mimeType: 'text/plain', buffer: Buffer.from('not an image') },
  ]);
  await expect(page.getByText(t.photos.uploading('test-lot.jpg'))).toBeVisible();
  await shoot(page, '07-sell-photos-uploading', 'light', { note: 'the upload routed to wait 4 s; two files refused before sending' });
  await expect(page.getByAltText(t.photos.alt(1, 1))).toBeVisible({ timeout: 20_000 });
  await page.unroute('**/api/v1/auctions/*/images');
  await shoot(page, '08-sell-photos', 'light', { note: 'the cover, and the refused files with their reasons' });

  await page.getByRole('button', { name: t.actions.publish }).click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await shoot(page, '09-sell-publish-confirm', 'light', { note: 'the schedule in local time with the zone', viewport: true });
  await page.getByRole('dialog').getByRole('button', { name: t.actions.publish }).click();
  await expect(page.getByText(t.actions.published)).toBeVisible();
  await shoot(page, '10-sell-scheduled', 'light', { note: 'published: everything can still change until it starts' });

  await page.getByLabel(f.title, { exact: true }).fill(`Hasselblad ${k} 500C with a hood`);
  await page.getByRole('link', { name: t.page.back }).click();
  await expect(page.getByRole('heading', { name: f.leaveTitle })).toBeVisible();
  await shoot(page, '11-sell-leave-unsaved', 'light', { note: 'leaving with an unsaved change', viewport: true });
  await page.getByRole('button', { name: f.leave }).click();

  await page.goto(`/sell/${scheduled}`);
  await page.getByRole('button', { name: t.actions.cancel }).click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await shoot(page, '12-sell-cancel-confirm', 'light', { note: 'names the listing; the API takes no reason', viewport: true });
  await page.getByRole('button', { name: t.actions.keep }).click();

  await page.goto(`/sell/${liveFree}`);
  await expect(page.getByText(t.page.descriptive)).toBeVisible();
  await shoot(page, '13-sell-live-no-bids', 'light', { note: 'only the title, description and photos can change (FR-17)' });
  await page.goto(`/sell/${liveBid}`);
  await expect(page.getByText(t.page.locked)).toBeVisible();
  await shoot(page, '14-sell-live-with-bids', 'light', { note: 'read-only after the first bid (BR-4)' });
  await page.goto(`/sell/${cancelled}`);
  await expect(page.getByText(enIN.cards.cancelledNote)).toBeVisible();
  await shoot(page, '17-sell-cancelled', 'light');

  for (const id of [sold, soldPaid]) await waitForStatus(request, id, 'closed_sold', 90_000);
  await waitForStatus(request, unsold, 'closed_no_sale', 90_000);
  await page.goto(`/sell/${sold}`);
  await expect(page.getByRole('region', { name: t.closed.title })).toContainText('Won by');
  await shoot(page, '15-sell-sold-awaiting', 'light', { note: 'the winner by name (BR-10) and the payment status (FR-69)' });
  await page.goto(`/sell/${unsold}`);
  await expect(page.getByText(t.closed.reserveNotMet, { exact: false })).toBeVisible();
  await shoot(page, '16-sell-not-sold', 'light', { note: 'the reserve was not met; its amount is the seller’s own' });
  await page.goto('/sell');
  await expect(page.getByRole('region', { name: t.dashboard.sections.closed })).toBeVisible();
  await shoot(page, '02-sell-dashboard', 'light', { note: 'drafts, scheduled, live and closing, closed with the outcome, cancelled' });
  await ctx.close();

  const dark = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: 'dark' });
  const dp = await dark.newPage();
  await signInUi(dp, seller);
  await dp.goto('/sell');
  await expect(dp.getByRole('region', { name: t.dashboard.sections.closed })).toBeVisible();
  await shoot(dp, '02-sell-dashboard', 'dark');
  await dp.goto(`/sell/${sold}`);
  await expect(dp.getByRole('region', { name: t.closed.title })).toContainText('Won by');
  await shoot(dp, '15-sell-sold-awaiting', 'dark');
  await dark.close();

  const other = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const op = await other.newPage();
  await signInUi(op, rival);
  await op.goto(`/sell/${scheduled}`);
  await expect(op.getByText(t.page.notYours)).toBeVisible();
  await shoot(op, '19-sell-not-yours', 'light', { note: 'another seller: no form, and the server sends no reserve' });
  await other.close();

  if (gateway) {
    await paid(request, buyer, soldPaid);
    const sp = await (await browser.newContext({ viewport: { width: 1440, height: 900 } })).newPage();
    await signInUi(sp, seller);
    await sp.goto(`/sell/${soldPaid}`);
    await expect(sp.getByRole('region', { name: t.closed.title })).toContainText('Paid on');
    await shoot(sp, '18-sell-sold-paid', 'light', { note: 'after a signed payment.captured webhook on a real sandbox order' });
    const bp = await (await browser.newContext({ viewport: { width: 1440, height: 900 } })).newPage();
    await signInUi(bp, buyer);
    await bp.goto(`/auctions/${soldPaid}`);
    await expect(bp.getByRole('link', { name: enIN.roomUi.bid.receipt })).toBeVisible();
    await shoot(bp, '20-room-won-paid', 'light', { note: 'the winner’s room: Paid on and the receipt instead of Pay' });
    await bp.goto(`/auctions/${sold}`);
    await expect(bp.getByRole('link', { name: enIN.roomUi.bid.pay('₹9,500') })).toBeVisible();
    await shoot(bp, '21-room-won-due', 'light', { note: 'not paid yet: Pay, as before' });
  } else {
    notShot.push('- **18, 20 and 21 (paid and due on the seller page and in the room)**: the api container could not reach api.razorpay.com, which creates the order; `e2e/room-paid.spec.ts` covers them when it can.');
  }
});

test('Admin and account: users, a suspension, denied, access changed, the audit diff, categories, the room panel; the account, a new password, your bids', async ({ browser, request }) => {
  test.setTimeout(300_000);
  const admin = await newAccount(request, 'admin', 'admin');
  const seller = await newAccount(request, 'seller', 'seller');
  const target = await newAccount(request, 'target');
  const bidder = await newAccount(request, 'bidder');
  const k = tag();
  const listed = await newAuction(request, seller, { title: `Audited ${k} Pentax`, startInS: 2, endInS: 1800, reservePrice: 1025000 });
  await request.patch(`${API}/auctions/${listed}`, { headers: bearer(seller), data: { reserve_price: 1100000 } });
  const other = await newAuction(request, seller, { title: `Olympus ${k} XA2`, startInS: 2, endInS: 1800, reservePrice: null });
  for (const id of [listed, other]) await waitForStatus(request, id, 'live');
  await (await restBid(request, bidder, listed, 900000)).json();
  await (await restProxy(request, bidder, listed, 1234567)).json();
  await (await restBid(request, target, listed, 950000)).json(); // the maximum answers: an automatic bid
  await (await restBid(request, bidder, other, 900000)).json();

  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  await signInUi(page, admin);
  await page.goto(`/admin/users?q=e2e-target`);
  await expect(page.locator('main li').first()).toBeVisible();
  await shoot(page, '22-admin-users', 'light', { note: 'search and filters in the URL; each action offered by the account’s state' });
  await page.getByRole('button', { name: adminEnIN.people.actionFor(adminEnIN.people.suspend, target.name), exact: true }).first().click();
  await page.getByRole('dialog').getByLabel(adminEnIN.room.reasonLabel).fill('shot: shill bidding');
  await shoot(page, '23-admin-suspend-confirm', 'light', { note: 'names the person; a reason is required (UI-8, BR-12)', viewport: true });
  await page.getByRole('button', { name: adminEnIN.people.keep }).click();
  await page.goto(`/admin/audit?type=auction&id=${listed}`);
  await expect(page.getByText('auction updated')).toBeVisible();
  await shoot(page, '26-admin-audit', 'light', { note: 'before and after as a diff; the reserve shown as hidden (Q2)' });
  await page.goto('/admin/categories');
  await expect(page.getByRole('button', { name: adminEnIN.categories.editLabel('Electronics') })).toBeVisible();
  await shoot(page, '27-admin-categories', 'light');
  await page.getByRole('button', { name: adminEnIN.categories.removeLabel('Electronics'), exact: true }).click();
  await page.getByRole('dialog').getByLabel(adminEnIN.room.reasonLabel).fill('shot: in use');
  await page.getByRole('dialog').getByRole('button', { name: adminEnIN.categories.remove }).click();
  await expect(page.getByRole('dialog').getByRole('alert')).toBeVisible();
  await shoot(page, '28-admin-category-in-use', 'light', { note: 'CATEGORY_IN_USE in the code’s words', viewport: true });
  await page.getByRole('button', { name: adminEnIN.categories.keep }).click();
  await page.goto(`/auctions/${other}`);
  await page.getByRole('button', { name: adminEnIN.room.forceClose, exact: true }).click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await shoot(page, '29-room-admin-force-close', 'light', { note: 'in the admin chunk, loaded for administrators only; names the lot', viewport: true });
  await page.getByRole('button', { name: adminEnIN.room.keepOpen }).click();
  await ctx.close();

  const dark = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: 'dark' });
  const dp = await dark.newPage();
  await signInUi(dp, admin);
  await dp.goto(`/admin/users?q=e2e-target`);
  await expect(dp.locator('main li').first()).toBeVisible();
  await shoot(dp, '22-admin-users', 'dark');
  await dp.goto(`/admin/audit?type=auction&id=${listed}`);
  await expect(dp.getByText('auction updated')).toBeVisible();
  await shoot(dp, '26-admin-audit', 'dark');
  await dark.close();

  // a bidder: denied in place; a role granted mid-session: access changed (the real 403)
  const bp = await (await browser.newContext({ viewport: { width: 1440, height: 900 } })).newPage();
  await signInUi(bp, bidder);
  await bp.goto('/admin');
  await expect(bp.getByRole('heading', { name: enIN.guards.deniedTitle })).toBeVisible();
  await shoot(bp, '24-admin-access-denied', 'light', { note: 'in place, no redirect, no admin file downloaded' });
  const promoted = await newAccount(request, 'promoted');
  const pp = await (await browser.newContext({ viewport: { width: 1440, height: 900 } })).newPage();
  await signInUi(pp, promoted);
  grantRole(promoted.email, 'admin');
  await pp.waitForTimeout(61_000); // the cached profile goes stale; the token still lacks the role
  await pp.evaluate(() => {
    history.pushState({}, '', '/admin/users');
    dispatchEvent(new PopStateEvent('popstate'));
  });
  await expect(pp.getByText(adminEnIN.access.title)).toBeVisible({ timeout: 15_000 });
  await shoot(pp, '25-admin-access-changed', 'light', { note: 'a role granted after sign-in: the API answered 403; Check again renews the token' });

  // the account
  await bp.goto('/account');
  await expect(bp.getByRole('heading', { name: accountEnIN.title, level: 1 })).toBeVisible();
  await shoot(bp, '30-account', 'light', { note: 'the name; email, status and roles read-only (FR-9); no avatar (C1)' });
  await bp.getByRole('button', { name: accountEnIN.password.send }).click();
  await expect(bp.getByText(accountEnIN.password.sent(bidder.email))).toBeVisible();
  await shoot(bp, '31-account-password-sent', 'light', { note: 'said before sending: it signs you out everywhere, this tab included (Q7)' });
  await bp.goto('/account/bids');
  await expect(bp.getByRole('link', { name: `Audited ${k} Pentax` }).first()).toBeVisible();
  await shoot(bp, '32-account-bids', 'light', { note: 'each auction named from its detail, four lookups at a time (Q3)' });
  const bd = await (await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: 'dark' })).newPage();
  await signInUi(bd, target);
  await bd.goto('/account');
  await expect(bd.getByRole('heading', { name: accountEnIN.title, level: 1 })).toBeVisible();
  await shoot(bd, '30-account', 'dark');
});

test.afterAll(() => {
  rows.sort((a, b) => a.state.localeCompare(b.state) || a.file.localeCompare(b.file));
  const esc = (s: string) => s.replace(/\|/g, '\\|').trim();
  const lines = [
    '# 06-slice4 shots',
    '',
    'The seller’s screens, the paid room, the administration and the account as built (`frontend/src/sell/`, `admin/`, `account/`, `room/BidControl.tsx`), rendered from real state on 8011 by `frontend/e2e/slice4-shots.spec.ts`. Every frame is a real state driven through the API and the UI; only the uploading frame routes a call (the upload held 4 s), and the access-changed frame is the real 403 after a role granted mid-session. PNGs are local (`design/` is git-ignored); re-make them with `cd frontend && npm run e2e -- e2e/slice4-shots.spec.ts`.',
    '',
    'Light at 1440 and 390 for every state; the dashboard, the sold listing, the users list, the audit log and the account also in dark.',
    '',
    '| State | File |',
    '|---|---|',
    ...rows.map((r) => `| ${r.state}${r.note ? ` (${esc(r.note)})` : ''} | ${r.file} |`),
    '',
    '## Not shot here',
    '',
    '- **LAST_ADMIN**: the e2e data has many active administrators, so the last one cannot be suspended or demoted here; the refusal is the server’s, shown in the code’s words like CANNOT_TARGET_SELF and NOT_SUSPENDED (`e2e/admin.spec.ts` checks NOT_SUSPENDED).',
    '- **The admin overview** (FR-81) and **Purchases**: not built; no backend source (gaps A1 and G9).',
    ...notShot,
    '',
  ];
  writeFileSync(`${OUT}/index.md`, lines.join('\n'));
});
