// Who reaches the administration (UI-2, FR-75, NFR-18): a non-admin sees access denied in place
// and never downloads admin code, in the section or in a room (the files read from the build's
// manifest); and the server decides: after a role granted mid-session the profile (read from the
// database) says administrator while the token does not, the API answers 403, the screen says the
// access changed, and "Check again" renews the token (the refresh reads the roles) and loads it.
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { expect, test, type Page } from '@playwright/test';

import { adminEnIN } from '../src/strings/admin-en-IN';
import { enIN } from '../src/strings/en-IN';
import { grantRole, newAccount, newAuction, signInUi, waitForStatus } from './fixtures/stack';

type Manifest = Record<string, { file: string; css?: string[] }>;
const manifest = JSON.parse(readFileSync(fileURLToPath(new URL('../dist/.vite/manifest.json', import.meta.url)), 'utf8')) as Manifest;
const ADMIN_FILES = ['src/admin/AdminArea.tsx', 'src/admin/RoomAdminPanel.tsx'].flatMap((k) => {
  const e = manifest[k];
  if (e === undefined) throw new Error(`${k} is not in the build manifest: run npm run build first`);
  return [e.file, ...(e.css ?? [])].map((f) => f.split('/').at(-1)!);
});

/** A navigation inside the app (no reload), as a click on a link would make. */
const go = (page: Page, path: string) =>
  page.evaluate((to) => {
    history.pushState({}, '', to);
    dispatchEvent(new PopStateEvent('popstate'));
  }, path);

test('a bidder sees access denied on every admin page and downloads no admin file, there or in a room', async ({ page, request }) => {
  test.setTimeout(60_000);
  const seller = await newAccount(request, 'seller', 'seller');
  const bidder = await newAccount(request, 'bidder');
  const id = await newAuction(request, seller, { startInS: 2, endInS: 600, reservePrice: null });
  await waitForStatus(request, id, 'live');
  const assets: string[] = [];
  page.on('request', (r) => r.url().includes('/assets/') && assets.push(r.url().split('/').at(-1)!));
  await signInUi(page, bidder);
  for (const path of ['/admin', '/admin/users', '/admin/audit', '/admin/categories']) {
    await page.goto(path);
    await expect(page.getByRole('heading', { name: enIN.guards.deniedTitle })).toBeVisible();
    await expect(page).toHaveURL(new RegExp(`${path}$`)); // in place, no redirect
  }
  await page.goto(`/auctions/${id}`);
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
  await expect(page.getByRole('heading', { name: adminEnIN.room.heading })).toHaveCount(0);
  expect(ADMIN_FILES.length).toBeGreaterThan(1);
  expect(assets.filter((a) => ADMIN_FILES.includes(a))).toEqual([]);
});

test('the server decides: a role granted mid-session gives 403 until the token is renewed; Check again renews it and the list loads', async ({ page, request }) => {
  test.setTimeout(150_000);
  const who = await newAccount(request, 'promoted');
  await signInUi(page, who); // the access token carries the bidder role only
  grantRole(who.email, 'admin'); // the database now says administrator
  await page.waitForTimeout(61_000); // the profile in the cache goes stale (60 s), so the guard reads it again

  await go(page, '/admin/users');
  await expect(page.getByText(adminEnIN.access.title)).toBeVisible({ timeout: 15_000 }); // 403 FORBIDDEN from the API
  await page.getByRole('button', { name: adminEnIN.access.retry }).click();
  await expect(page.getByRole('heading', { name: adminEnIN.users.title, level: 2 })).toBeVisible();
  await expect(page.locator('main li').first()).toBeVisible();
  await expect(page.getByText(adminEnIN.access.title)).toHaveCount(0);
});
