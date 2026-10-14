// The identity flow on 8011 under the real CSP (brief 9.10, 13 items 22, 23, 29): register,
// verify, sign in and out; a wrong password is one generic message; forgot and reset round-trip
// through Mailpit; RequireAuth returns to the same URL; RequireRole denies access without a loop.
// Amendment 1: the verify and reset tokens travel only in the URL fragment, so they never reach
// the web service's log and no browser request carries them except the two auth request bodies.
import { expect, test, type Page } from '@playwright/test';

import { enIN } from '../src/strings/en-IN';
import { API, mailToken, newAccount, watchTokenLeaks, webLogsSince } from './fixtures/stack';

const tag = () => Math.random().toString(36).slice(2, 8);

async function register(page: Page, email: string, password: string) {
  await page.goto('/register');
  await page.getByLabel(enIN.auth.register.name).fill('Ada Lovelace');
  await page.getByLabel(enIN.auth.register.email).fill(email);
  await page.getByLabel(enIN.auth.register.password).fill(password);
  await page.getByRole('button', { name: enIN.auth.register.submit }).click();
  await expect(page.getByRole('heading', { name: enIN.auth.checkEmail.title })).toBeVisible();
}

async function signIn(page: Page, email: string, password: string) {
  await page.goto('/sign-in');
  await page.getByLabel(enIN.auth.signIn.email).fill(email);
  await page.getByLabel(enIN.auth.signIn.password).fill(password);
  await page.getByRole('button', { name: enIN.auth.signIn.submit, exact: true }).click();
}

test('register, verify from the fragment, sign in and out; the token never reaches a server log', async ({ page, request }) => {
  const email = `e2e-ada-${tag()}@example.com`;
  const password = `Pw-${tag()}-${tag()}-9`;
  let verifyToken = '';
  const leaks = watchTokenLeaks(page, () => [verifyToken]);
  const since = new Date().toISOString();

  await register(page, email, password);
  verifyToken = await mailToken(request, email, 'verify-email');

  await page.goto(`/verify-email#token=${verifyToken}`);
  await expect(page.getByText(enIN.auth.verify.done)).toBeVisible();
  // the fragment is stripped before anything renders (item 23)
  expect(await page.evaluate(() => location.hash)).toBe('');
  expect(await page.evaluate(() => location.search)).toBe('');

  await page.getByRole('main').getByRole('link', { name: enIN.auth.verify.signIn }).click();
  await signIn(page, email, password);
  await page.waitForURL('http://localhost:8011/');
  await expect(page.getByRole('button', { name: enIN.auth.signOut.label })).toBeVisible();

  await page.getByRole('button', { name: enIN.auth.signOut.label }).click();
  await expect(page.getByRole('link', { name: enIN.nav.signIn })).toBeVisible();

  // Amendment 1: no server log holds the token, and no request carried it but the API body
  expect(webLogsSince(since)).not.toContain(verifyToken);
  expect(leaks).toEqual([]);
});

test('a wrong password is one generic message', async ({ page, request }) => {
  const a = await newAccount(request, 'wrongpw');
  await signIn(page, a.email, 'not-the-password');
  await expect(page.getByText(enIN.errors.INVALID_CREDENTIALS)).toBeVisible();
  await expect(page).toHaveURL(/\/sign-in$/);
});

test('forgot and reset round-trip through the fragment; the token never reaches a server log', async ({ page, request }) => {
  const a = await newAccount(request, 'reset');
  let resetToken = '';
  const leaks = watchTokenLeaks(page, () => [resetToken]);
  const since = new Date().toISOString();

  await page.goto('/forgot-password');
  await page.getByLabel(enIN.auth.forgot.email).fill(a.email);
  await page.getByRole('button', { name: enIN.auth.forgot.submit }).click();
  await expect(page.getByText(enIN.auth.forgot.done)).toBeVisible();

  resetToken = await mailToken(request, a.email, 'reset-password');
  const next = `Pw-${tag()}-${tag()}-new`;
  await page.goto(`/reset-password#token=${resetToken}`);
  await page.getByLabel(enIN.auth.reset.password).fill(next);
  await page.getByRole('button', { name: enIN.auth.reset.submit }).click();
  await expect(page.getByText(enIN.auth.reset.done)).toBeVisible();
  expect(await page.evaluate(() => location.hash)).toBe('');

  await signIn(page, a.email, next);
  await page.waitForURL('http://localhost:8011/');

  expect(webLogsSince(since)).not.toContain(resetToken);
  expect(leaks).toEqual([]);
});

test('RequireAuth redirects an anonymous visitor to sign in and back to the same URL', async ({ page, request }) => {
  const a = await newAccount(request, 'guard');
  await page.goto('/account');
  await page.waitForURL(/\/sign-in$/);
  await page.getByLabel(enIN.auth.signIn.email).fill(a.email);
  await page.getByLabel(enIN.auth.signIn.password).fill(a.password);
  await page.getByRole('button', { name: enIN.auth.signIn.submit, exact: true }).click();
  await page.waitForURL(/\/account$/);
  await expect(page.getByRole('heading', { name: enIN.placeholder.title })).toBeVisible();
});

// Q1: a refresh that cannot reach the server (5xx) is not a sign-out; 401 still is
test('RequireAuth offers a retry when the load-time refresh fails with a 5xx, and redirects on a 401', async ({ page, request }) => {
  const a = await newAccount(request, 'reach');
  await signIn(page, a.email, a.password);
  await page.waitForURL('http://localhost:8011/');
  await page.route('**/api/v1/auth/refresh', (r) => r.fulfill({ status: 503, contentType: 'application/json', body: '{}' }));
  await page.goto('/account');
  await expect(page.getByRole('heading', { name: enIN.guards.unreachableTitle })).toBeVisible();
  await expect(page).toHaveURL(/\/account$/);

  // the server is back: the retry restores the session in place, still on the same URL
  await page.unroute('**/api/v1/auth/refresh');
  await page.getByRole('button', { name: enIN.common.tryAgain }).click();
  await expect(page.getByRole('heading', { name: enIN.placeholder.title })).toBeVisible();
  await expect(page).toHaveURL(/\/account$/);

  // a 401 says the session is gone: that is a sign-out, so sign in again
  await page.route('**/api/v1/auth/refresh', (r) =>
    r.fulfill({ status: 401, contentType: 'application/json', body: '{"error":{"code":"INVALID_TOKEN","message":"gone"}}' }),
  );
  await page.goto('/account');
  await page.waitForURL(/\/sign-in$/);
});

test('RequireRole shows access denied in place, with no redirect loop', async ({ page, request }) => {
  const a = await newAccount(request, 'norole');
  await signIn(page, a.email, a.password);
  await page.waitForURL('http://localhost:8011/');
  await page.goto('/sell');
  await expect(page.getByRole('heading', { name: enIN.guards.deniedTitle })).toBeVisible();
  await expect(page).toHaveURL(/\/sell$/);
});

test('the identity screens are reached under the same CSP as the shell', async ({ page }) => {
  await page.addInitScript(() => {
    (window as unknown as { v: string[] }).v = [];
    document.addEventListener('securitypolicyviolation', (e) => (window as unknown as { v: string[] }).v.push(e.violatedDirective));
  });
  const errors: string[] = [];
  page.on('console', (m) => m.type() === 'error' && errors.push(m.text()));
  page.on('pageerror', (e) => errors.push(e.message));
  for (const route of ['/register', '/sign-in', '/forgot-password']) {
    await page.goto(route);
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
  }
  expect(errors).toEqual([]);
  expect(await page.evaluate(() => (window as unknown as { v: string[] }).v)).toEqual([]);
  expect(API).toContain('/api/v1');
});
