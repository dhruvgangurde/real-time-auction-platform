// The account on 8011 (brief 9.1, 9.10; FR-9, FR-8, FR-5, FR-10, UI-7, NFR-35): the name changed
// and shown in the header; the user's pages linked by role; the email choices reached; a new
// password by the reset email, said first to sign out everywhere, after which a room open in
// another tab closes with 4004 and this tab needs a sign-in; sign out at 390 from the keyboard;
// "Your bids" named from each auction, "Auction" with its link when a lookup fails (Q3).
import { expect, test } from '@playwright/test';

import { accountEnIN as t } from '../src/strings/account-en-IN';
import { enIN } from '../src/strings/en-IN';
import { API, mailToken, newAccount, newAuction, restBid, restProxy, signInUi, waitForLive, waitForStatus } from './fixtures/stack';

test('the name, changed and shown in the header; the pages linked by role; the email choices reached', async ({ page, request }) => {
  const who = await newAccount(request, 'asha', 'seller');
  await signInUi(page, who);
  await page.getByRole('banner').getByRole('link', { name: who.name, exact: true }).click();
  await page.waitForURL('**/account');
  await expect(page.getByRole('heading', { name: t.title, level: 1 })).toBeVisible();
  await expect(page.getByText(who.email)).toBeVisible();
  await page.getByLabel(t.profile.name).fill('   ');
  await page.getByRole('button', { name: t.profile.save }).click();
  await expect(page.getByText(t.profile.problems.required)).toBeVisible();
  await expect(page.getByLabel(t.profile.name)).toBeFocused();
  await page.getByLabel(t.profile.name).fill('Asha Rao');
  await page.getByRole('button', { name: t.profile.save }).click();
  await expect(page.getByText(t.profile.saved)).toBeVisible();
  await expect(page.getByRole('banner').getByRole('link', { name: 'Asha Rao', exact: true })).toBeVisible();
  const links = page.getByRole('region', { name: t.links.title }).getByRole('link');
  await expect(links).toHaveText([t.links.bids, t.links.listings, t.links.email]); // a seller, not an administrator
  await page.getByRole('link', { name: t.links.email }).click();
  await page.waitForURL('**/notifications#email-title');
  await expect(page.getByRole('heading', { name: enIN.emailPrefs.title })).toBeInViewport();
});

test('a new password by email, said first to sign out everywhere: after the reset another tab’s room closes 4004 and this tab needs a sign-in', async ({ browser, request }) => {
  test.setTimeout(120_000);
  const seller = await newAccount(request, 'seller', 'seller');
  const who = await newAccount(request, 'reset');
  const id = await newAuction(request, seller, { startInS: 2, endInS: 600, reservePrice: null });
  await waitForStatus(request, id, 'live');
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const account = await ctx.newPage();
  await signInUi(account, who);
  const room = await ctx.newPage(); // another tab of the same browser: the same session
  await room.goto(`/auctions/${id}`);
  await waitForLive(room);

  await account.goto('/account');
  await expect(account.getByText(t.password.note)).toBeVisible(); // said before anything is sent (Q7)
  await account.getByRole('button', { name: t.password.send }).click();
  await expect(account.getByText(t.password.sent(who.email))).toBeVisible();
  const token = await mailToken(request, who.email, 'reset-password');
  expect((await request.post(`${API}/auth/reset-password`, { data: { token, password: `Pw-${Date.now()}-new-1` } })).ok()).toBe(true);

  await expect(room.getByText(enIN.roomUi.banner.signedOut, { exact: true })).toBeVisible({ timeout: 20_000 }); // 4004
  await account.reload();
  await account.waitForURL(/\/sign-in$/); // this tab's session ended too
  await ctx.close();
});

test('at 390 in dark, from the keyboard: the account fits and Sign out signs this browser out', async ({ browser, request }) => {
  const who = await newAccount(request, 'phone');
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, colorScheme: 'dark' });
  const page = await ctx.newPage();
  await signInUi(page, who);
  await page.goto('/account');
  await expect(page.locator('body')).toHaveCSS('background-color', 'rgb(13, 20, 36)');
  expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);
  const signOut = page.getByRole('main').getByRole('button', { name: enIN.auth.signOut.label });
  await page.getByRole('button', { name: t.password.send }).focus();
  await page.keyboard.press('Tab');
  await expect(signOut).toBeFocused();
  await page.keyboard.press('Enter');
  await page.waitForURL('http://localhost:8011/');
  await expect(page.getByRole('banner').getByRole('link', { name: enIN.nav.signIn })).toBeVisible();
  await ctx.close();
});

test('your bids: each auction by its title, the bids a maximum placed marked; a lookup that fails says Auction with its link', async ({ page, request }) => {
  test.setTimeout(120_000);
  const seller = await newAccount(request, 'seller', 'seller');
  const who = await newAccount(request, 'bidder');
  const other = await newAccount(request, 'other');
  const a = await newAuction(request, seller, { title: 'Rollei 35 with its case', startInS: 2, endInS: 600, reservePrice: null });
  const b = await newAuction(request, seller, { title: 'Olympus XA2', startInS: 2, endInS: 600, reservePrice: null });
  for (const id of [a, b]) await waitForStatus(request, id, 'live');
  await (await restBid(request, who, a, 900000)).json();
  await (await restProxy(request, who, a, 1234567)).json();
  await (await restBid(request, other, a, 950000)).json(); // the maximum answers it: an automatic bid
  await (await restBid(request, who, b, 900000)).json();
  // the lookup of b fails (a stand-in 429): its rows still link to the room, named "Auction"
  await page.route(`**/api/v1/auctions/${b}`, (route) =>
    route.fulfill({ status: 429, contentType: 'application/json', body: '{"error":{"code":"RATE_LIMITED","message":"limited"}}' }),
  );
  await signInUi(page, who);
  await page.goto('/account/bids');
  await expect(page.getByRole('link', { name: 'Rollei 35 with its case' }).first()).toBeVisible();
  await expect(page.getByText(t.bids.byMaximum)).toHaveCount(1);
  const fallback = page.getByRole('link', { name: t.bids.auction, exact: true });
  await expect(fallback).toHaveAttribute('href', `/auctions/${b}`);
  await expect(page.getByRole('alert')).toHaveCount(0); // never an error row
});
