// The administration section on 8011 (brief 9.9; FR-75, FR-76, FR-80, FR-20, FR-63, UI-8, BR-12,
// BR-6): users with their filters in the URL; suspend (the account's open room closes with 4004),
// reinstate, roles given and taken, each confirmed with the person named and a reason; a stale row
// refused in the code's own words; the audit log with a reserve shown as "hidden", Show more, and
// its link to the room; the category tree changed with reasons; the room's panel by status; 390,
// dark and the keyboard.
import { expect, test, type Page } from '@playwright/test';

import { adminEnIN } from '../src/strings/admin-en-IN';
import { enIN } from '../src/strings/en-IN';
import { API, bearer, newAccount, newAuction, signInUi, waitForLive, waitForStatus, type Account } from './fixtures/stack';

const u = adminEnIN.users;
const p = adminEnIN.people;
const tag = () => Math.random().toString(36).slice(2, 7);
const row = (page: Page, who: Account) => page.locator('li').filter({ hasText: who.email });

/** Open the person's action, give the reason, confirm. */
async function act(page: Page, who: Account, action: string, reason: string) {
  await page.getByRole('button', { name: p.actionFor(action, who.name), exact: true }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByLabel(adminEnIN.room.reasonLabel).fill(reason);
  await dialog.getByRole('button', { name: action, exact: true }).click();
}

test('users: filters in the URL; suspend ends their sessions (their room closes 4004), reinstate, a role given and taken; a stale row is refused in the code’s words', async ({ browser, request }) => {
  test.setTimeout(150_000);
  const admin = await newAccount(request, 'admin', 'admin');
  const target = await newAccount(request, 'target');
  const seller = await newAccount(request, 'seller', 'seller');
  const id = await newAuction(request, seller, { startInS: 2, endInS: 600, reservePrice: null });
  await waitForStatus(request, id, 'live');

  // the target, in a live room on its own session
  const theirs = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const room = await theirs.newPage();
  await signInUi(room, target);
  await room.goto(`/auctions/${id}`);
  await waitForLive(room);

  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  await signInUi(page, admin);
  await page.goto(`/admin/users?q=${encodeURIComponent(target.email)}&status=active`);
  await expect(row(page, target)).toContainText(u.statuses.active);
  await page.reload(); // the filters come back from the URL
  await expect(page.getByLabel(u.q)).toHaveValue(target.email);
  await expect(page.getByLabel(u.status)).toHaveValue('active');

  // suspend: named, a reason, and the target's open room is signed out at once
  await page.getByRole('button', { name: p.actionFor(p.suspend, target.name), exact: true }).click();
  await expect(page.getByRole('heading', { name: p.suspendTitle(target.name, target.email) })).toBeVisible();
  await expect(page.getByRole('dialog').getByRole('button', { name: p.suspend, exact: true })).toHaveAttribute('aria-disabled', 'true'); // no reason yet
  await page.getByRole('dialog').getByLabel(adminEnIN.room.reasonLabel).fill('e2e: shill bidding');
  await page.getByRole('dialog').getByRole('button', { name: p.suspend, exact: true }).click();
  await expect(page.getByText(p.suspended(target.name))).toBeVisible();
  await expect(room.getByText(enIN.roomUi.banner.signedOut, { exact: true })).toBeVisible({ timeout: 20_000 });
  await theirs.close();

  // the filter still says active, so the list is read again without them; the suspended view has them
  await page.goto(`/admin/users?q=${encodeURIComponent(target.email)}&status=suspended`);
  await expect(row(page, target)).toContainText(u.statuses.suspended);

  // a stale row: reinstated meanwhile elsewhere, so this Reinstate is refused with NOT_SUSPENDED
  expect((await request.post(`${API}/admin/users/${target.id}/reinstate`, { headers: bearer(admin), data: { reason: 'e2e: elsewhere' } })).status()).toBe(200);
  await act(page, target, p.reinstate, 'e2e: again');
  await expect(page.getByRole('dialog').getByRole('alert')).toHaveText(enIN.errors.NOT_SUSPENDED);
  await page.getByRole('button', { name: p.keep }).click();

  // a role given, then taken; the row says so each time
  await page.goto(`/admin/users?q=${encodeURIComponent(target.email)}`);
  await act(page, target, p.grant(p.roleNoun.seller), 'e2e: approved seller');
  await expect(page.getByText(p.granted(p.roleNoun.seller, target.name))).toBeVisible();
  await expect(row(page, target)).toContainText(u.roles.seller);
  await act(page, target, p.revoke(p.roleNoun.seller), 'e2e: no longer');
  await expect(page.getByText(p.revoked(p.roleNoun.seller, target.name))).toBeVisible();
  await expect(row(page, target)).not.toContainText(u.roles.seller);

  // oneself: no Suspend and no removing one's own administrator role (the server refuses both anyway)
  await page.goto(`/admin/users?q=${encodeURIComponent(admin.email)}`);
  await expect(row(page, admin)).toContainText(u.you);
  await expect(row(page, admin).getByRole('button', { name: new RegExp(`^${p.suspend}|${p.revoke(p.roleNoun.admin)}`) })).toHaveCount(0);
  await ctx.close();
});

test('audit: an auction’s entries with its reserve shown as hidden, never the amount; Show more; the entry links to its room', async ({ page, request }) => {
  test.setTimeout(90_000);
  const admin = await newAccount(request, 'admin', 'admin');
  const seller = await newAccount(request, 'seller', 'seller');
  const id = await newAuction(request, seller, { title: `Audited ${tag()} Pentax`, startInS: 3600, endInS: 7200, reservePrice: 1025000 });
  await request.patch(`${API}/auctions/${id}`, { headers: bearer(seller), data: { reserve_price: 1100000 } });
  const bodies: string[] = [];
  page.on('response', async (r) => r.url().includes('/admin/audit') && bodies.push(await r.text()));
  await signInUi(page, admin);
  await page.goto(`/admin/audit?type=auction&id=${id}`);
  await expect(page.getByText('auction updated')).toBeVisible();
  const text = await page.locator('main').innerText();
  expect(text).toContain(adminEnIN.audit.hidden);
  expect(text).not.toMatch(/1025000|1100000|10,250|11,000/); // BR-6, Q2: never on screen
  expect(bodies.join('')).toContain('1025000'); // the API still sends it to administrators: gap A3
  await page.getByRole('link', { name: adminEnIN.audit.entity(adminEnIN.audit.kinds.auction, id.slice(0, 8)) }).first().click();
  await page.waitForURL(`**/auctions/${id}`);

  await page.goto('/admin/audit?type=auction');
  const entries = page.locator('main li');
  await expect(entries).toHaveCount(50);
  await page.getByRole('button', { name: adminEnIN.audit.more }).click();
  await expect.poll(() => entries.count()).toBeGreaterThan(50);
});

test('categories: create, rename and move with a reason; a used one cannot be deleted; the new one is', async ({ page, request }) => {
  test.setTimeout(60_000);
  const c = adminEnIN.categories;
  const admin = await newAccount(request, 'admin', 'admin');
  const k = tag();
  await signInUi(page, admin);
  await page.goto('/admin/categories');
  const create = page.getByRole('region', { name: c.newTitle });
  await create.getByLabel(c.name).fill(`Tripods ${k}`);
  await create.getByLabel(c.slug).fill(`Tripods ${k}`); // not a slug: refused here
  await create.getByLabel(adminEnIN.room.reasonLabel).fill('e2e: tripods');
  await create.getByRole('button', { name: c.create }).click();
  await expect(create.getByText(c.problems.slug)).toBeVisible();
  await create.getByLabel(c.slug).fill(`tripods-${k}`);
  await create.getByRole('button', { name: c.create }).click();
  await expect(page.getByText(c.created(`Tripods ${k}`))).toBeVisible();

  await page.getByRole('button', { name: c.editLabel(`Tripods ${k}`) }).click();
  const edit = page.getByRole('region', { name: c.editTitle(`Tripods ${k}`) });
  await edit.getByLabel(c.name).fill(`Tripods and heads ${k}`);
  await edit.getByLabel(c.parent).selectOption({ label: 'Electronics' });
  await edit.getByLabel(adminEnIN.room.reasonLabel).fill('e2e: under electronics');
  await edit.getByRole('button', { name: c.save }).click();
  await expect(page.getByText(c.saved(`Tripods and heads ${k}`))).toBeVisible();
  const tree = (await (await request.get(`${API}/categories`)).json()) as { slug: string; children: { slug: string }[] }[];
  expect(tree.find((n) => n.slug === 'electronics')?.children.map((n) => n.slug)).toContain(`tripods-${k}`);

  await page.getByRole('button', { name: c.removeLabel('Electronics'), exact: true }).click();
  await page.getByRole('dialog').getByLabel(adminEnIN.room.reasonLabel).fill('e2e: in use');
  await page.getByRole('dialog').getByRole('button', { name: c.remove }).click();
  await expect(page.getByRole('dialog').getByRole('alert')).toHaveText(enIN.errors.CATEGORY_IN_USE);
  await page.getByRole('button', { name: c.keep }).click();
  await page.getByRole('button', { name: c.removeLabel(`Tripods and heads ${k}`) }).click();
  await expect(page.getByRole('heading', { name: c.removeTitle(`Tripods and heads ${k}`) })).toBeVisible();
  await page.getByRole('dialog').getByLabel(adminEnIN.room.reasonLabel).fill('e2e: clean up');
  await page.getByRole('dialog').getByRole('button', { name: c.remove }).click();
  await expect(page.getByText(c.removed(`Tripods and heads ${k}`))).toBeVisible();
});

test('the room’s admin panel offers each action only where the API allows it and names the lot', async ({ page, request }) => {
  test.setTimeout(90_000);
  const r = adminEnIN.room;
  const admin = await newAccount(request, 'admin', 'admin');
  const seller = await newAccount(request, 'seller', 'seller');
  const live = await newAuction(request, seller, { title: `Live ${tag()} Nikon`, startInS: 2, endInS: 600, reservePrice: null });
  const later = await newAuction(request, seller, { title: `Later ${tag()} Leica`, startInS: 3600, endInS: 7200 });
  await waitForStatus(request, live, 'live');
  await signInUi(page, admin);
  await page.goto(`/auctions/${later}`);
  await expect(page.getByRole('heading', { name: r.heading })).toBeVisible();
  await expect(page.getByRole('button', { name: r.cancel })).toBeVisible();
  await expect(page.getByRole('button', { name: r.forceClose, exact: true })).toHaveCount(0); // AUCTION_NOT_LIVE
  await page.goto(`/auctions/${live}`);
  await page.getByRole('button', { name: r.forceClose, exact: true }).click();
  await expect(page.getByRole('heading', { name: r.forceTitle((await page.locator('h1').first().textContent()) ?? '') })).toBeVisible();
  await page.getByLabel(r.reasonLabel).fill('e2e: closing for the test');
  await page.getByRole('dialog').getByRole('button', { name: r.forceClose, exact: true }).click();
  await waitForStatus(request, live, 'closed_no_sale', 60_000);
  await page.reload();
  await expect(page.getByRole('heading', { name: r.heading })).toBeVisible();
  await expect(page.getByRole('button', { name: new RegExp(`${r.forceClose}|${r.cancel}`) })).toHaveCount(0); // finished
});

test('at 390 in dark, from the keyboard: the users list fits; Tab to an action, Enter opens it with the reason focused, Escape closes it and gives the focus back', async ({ browser, request }) => {
  test.setTimeout(60_000);
  const admin = await newAccount(request, 'admin', 'admin');
  const target = await newAccount(request, 'target');
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, colorScheme: 'dark' });
  const page = await ctx.newPage();
  await signInUi(page, admin);
  await page.goto(`/admin/users?q=${encodeURIComponent(target.email)}`);
  await expect(row(page, target)).toBeVisible();
  await expect(page.locator('body')).toHaveCSS('background-color', 'rgb(13, 20, 36)');
  expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);
  const suspend = page.getByRole('button', { name: p.actionFor(p.suspend, target.name), exact: true });
  await suspend.focus();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('dialog').getByLabel(adminEnIN.room.reasonLabel)).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(suspend).toBeFocused();
  await ctx.close();
});
