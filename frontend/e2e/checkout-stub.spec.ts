// Checkout (brief 9.5; FR-65 to FR-69, DC-10, NFR-25) on 8011 with Razorpay's checkout.js replaced
// by a stand-in served at its own URL, so the payment page's enforced CSP applies unchanged. The
// backend is real: POST /payment creates a real sandbox order, so the tests that open the window
// on a real order skip when the api container cannot reach api.razorpay.com (the beforeAll
// pre-check, and only that). The checkout callback and the webhooks are signed as Razorpay signs
// them, with the secrets read from the api container at run time.
import { expect, test, type APIRequestContext, type Browser, type Page } from '@playwright/test';

import { enIN } from '../src/strings/en-IN';
import { payEnIN as p } from '../src/strings/pay-en-IN';
import { CHECKOUT_SRC, apiReachesRazorpay, checkoutIds, newPaymentId, sendWebhook, stubCheckout, watchViolations } from './fixtures/pay';
import { newAccount, newAuction, restBid, signInUi, waitForStatus, type Account } from './fixtures/stack';

const GATEWAY = 'skipped: the api container cannot reach api.razorpay.com, which creates the order (beforeAll pre-check)';
let reachable = false;
test.beforeAll(() => {
  reachable = apiReachesRazorpay();
});

type Rzp = { options: { key: string; order_id: string; amount: number; currency: string; handler: (r: unknown) => void; modal: { ondismiss: () => void } }; failed: ((r: unknown) => void)[] };
type Win = { __rzp: Rzp; __rzpOpened?: number };
const opened = (page: Page) => page.evaluate(() => (window as unknown as Win).__rzpOpened ?? 0);
const orderId = (page: Page) => page.evaluate(() => (window as unknown as Win).__rzp.options.order_id);
const dismiss = (page: Page) => page.evaluate(() => (window as unknown as Win).__rzp.options.modal.ondismiss());

/** A closed auction that `winner` won at ₹9,500 against `other`. */
async function wonAuction(request: APIRequestContext, title: string) {
  const seller = await newAccount(request, 'seller', 'seller');
  const winner = await newAccount(request, 'winner');
  const other = await newAccount(request, 'other');
  const id = await newAuction(request, seller, { title, startInS: 2, endInS: 25, thresholdS: 10, extensionS: 10, reservePrice: null });
  await waitForStatus(request, id, 'live');
  await (await restBid(request, other, id, 900000)).json();
  await (await restBid(request, winner, id, 950000)).json();
  return { id, seller, winner, other };
}

async function signedIn(browser: Browser, who: Account, width = 1440) {
  const ctx = await browser.newContext({ viewport: { width, height: width < 500 ? 844 : 900 } });
  const page = await ctx.newPage();
  await signInUi(page, who);
  return { ctx, page };
}

test("from the room's Pay: closing the window comes back to due; a failure inside it, then Try again, on the same order", async ({ browser, request }) => {
  test.skip(!reachable, GATEWAY);
  test.setTimeout(150_000);
  const title = `Rollei ${Math.random().toString(36).slice(2, 7)} 35`;
  const { id, winner } = await wonAuction(request, title);
  await waitForStatus(request, id, 'closed_sold', 60_000);
  const { ctx, page } = await signedIn(browser, winner);
  const violations = await watchViolations(page);
  await stubCheckout(page);
  const starts: (string | null)[] = [];
  page.on('request', (r) => r.method() === 'POST' && r.url().endsWith(`/auctions/${id}/payment`) && starts.push(r.postData()));

  // the room's Pay opens the payment page, its own document
  await page.goto(`/auctions/${id}`);
  await page.getByRole('link', { name: 'Pay ₹9,500' }).click();
  await page.waitForURL(`**/auctions/${id}/pay`);
  await expect(page).toHaveTitle('Payment, Real-Time Auction');
  const pay = page.getByRole('button', { name: p.pay('₹9,500') });
  await expect(pay).toBeVisible();
  await expect(pay).not.toBeFocused(); // on load the page reads from the top; focus moves only on the way back
  await pay.click();
  await expect.poll(() => opened(page)).toBe(1);
  const options = await page.evaluate(() => {
    const o = (window as unknown as Win).__rzp.options as Record<string, unknown>;
    return { key: o.key, order_id: o.order_id, amount: o.amount, currency: o.currency, prefill: 'prefill' in o };
  });
  expect(options).toMatchObject({ amount: 950000, currency: 'INR', prefill: false }); // the server's order, nothing personal
  expect(String(options.key)).toMatch(/^rzp_test_/);
  expect(String(options.order_id)).toMatch(/^order_/);
  await expect(page.getByText(p.open)).toBeVisible();

  // closed without paying: back to due, the button has the focus
  await dismiss(page);
  await expect(page.getByText(p.dismissed)).toBeVisible();
  await expect(pay).toBeFocused();

  // a failure inside the window, then closing it: Failed; Try again opens it on the same order
  await pay.click();
  await expect.poll(() => opened(page)).toBe(2);
  expect(await orderId(page)).toBe(options.order_id);
  await page.evaluate(() => {
    const rzp = (window as unknown as Win).__rzp;
    for (const listener of rzp.failed) listener({ error: { code: 'BAD_REQUEST_ERROR' } });
    rzp.options.modal.ondismiss();
  });
  await expect(page.getByRole('heading', { name: p.failedTitle })).toBeVisible();
  await page.getByRole('button', { name: p.tryAgain }).click();
  await expect.poll(() => opened(page)).toBe(3);
  expect(await orderId(page)).toBe(options.order_id);
  await dismiss(page);
  expect(starts).toEqual([null, null, null]); // no body at all: never an amount (FR-66)

  // Razorpay reports the failed attempt by webhook: the next visit says so, and its notification opens the page
  expect(await sendWebhook(request, 'payment.failed', { id: newPaymentId(), order_id: String(options.order_id), amount: 950000 })).toBe(200);
  await page.reload();
  await expect(page.getByText(p.lastFailed)).toBeVisible();
  await page.goto('/notifications');
  await page.getByText(enIN.notificationsPage.text.paymentFailed('₹9,500', title)).click();
  await page.waitForURL(`**/auctions/${id}/pay`);
  await expect(pay).toBeVisible();
  expect(violations).toEqual([]);
  await ctx.close();
});

test('a signed checkout callback leaves it confirming until a signed webhook pays it; the receipt; the seller sees it paid; the other bidder is refused', async ({ browser, request }) => {
  test.skip(!reachable, GATEWAY);
  test.setTimeout(150_000);
  const { id, seller, winner, other } = await wonAuction(request, `Leica ${Math.random().toString(36).slice(2, 7)} CL`);
  await waitForStatus(request, id, 'closed_sold', 60_000);
  const { ctx, page } = await signedIn(browser, winner);
  const violations = await watchViolations(page);
  await stubCheckout(page);
  const confirms: string[] = [];
  page.on('request', (r) => r.url().endsWith(`/auctions/${id}/payment/confirm`) && confirms.push(r.postData() ?? ''));
  await page.goto(`/auctions/${id}/pay`);
  await page.getByRole('button', { name: p.pay('₹9,500') }).click();
  await expect.poll(() => opened(page)).toBe(1);
  const order = await orderId(page);

  // the window's success handler: ids posted, the server cannot see the payment yet (202)
  const paymentId = newPaymentId();
  await page.evaluate((ids) => (window as unknown as Win).__rzp.options.handler(ids), checkoutIds(order, paymentId));
  await expect(page.getByRole('heading', { name: p.confirmingTitle })).toBeVisible();
  await expect.poll(() => confirms.length).toBe(1);
  expect(Object.keys(JSON.parse(confirms[0]!) as object)).toEqual(['razorpay_order_id', 'razorpay_payment_id', 'razorpay_signature']);
  await page.waitForTimeout(3_000); // a settlement read or two later: still not paid
  await expect(page.getByRole('heading', { name: p.paidTitle })).toHaveCount(0);

  // the webhook confirms it; the page's settlement read turns it into the receipt
  expect(await sendWebhook(request, 'payment.captured', { id: paymentId, order_id: order, amount: 950000 })).toBe(200);
  await expect(page.getByRole('heading', { name: p.paidTitle })).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText(p.paid('₹9,500'))).toBeVisible();
  await expect(page.getByText(p.paymentId(paymentId))).toBeVisible();
  await page.reload();
  await expect(page.getByRole('heading', { name: p.paidTitle })).toBeVisible();
  expect(violations).toEqual([]);
  await ctx.close();

  // the seller reads the status (FR-69); the other bidder is refused (FR-65)
  for (const [who, line] of [
    [seller, new RegExp(`^${winner.name} paid ₹9,500 on `)],
    [other, new RegExp(`^${p.notWinner}$`)],
  ] as const) {
    const s = await signedIn(browser, who);
    await s.page.goto(`/auctions/${id}/pay`);
    await expect(s.page.getByText(line)).toBeVisible();
    await s.ctx.close();
  }
});

test('with no order: a signed-out visit comes back through sign-in, refused starts say why, the script that does not load, no sale; 390, dark, keyboard', async ({ browser, request }) => {
  test.setTimeout(150_000);
  const { id, winner } = await wonAuction(request, `Nikon ${Math.random().toString(36).slice(2, 7)} FM2`);
  const seller = await newAccount(request, 'seller', 'seller');
  const unsold = await newAuction(request, seller, { startInS: 2, endInS: 25, thresholdS: 10, extensionS: 10, reservePrice: null });
  await waitForStatus(request, id, 'closed_sold', 60_000);
  await waitForStatus(request, unsold, 'closed_no_sale', 60_000);

  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, colorScheme: 'dark' });
  const page = await ctx.newPage();
  const violations = await watchViolations(page);
  await stubCheckout(page);

  // signed out: sign in, then back to this page through ?next= (a full load)
  await page.goto(`/auctions/${id}/pay`);
  await expect(page.getByRole('heading', { name: p.signInTitle })).toBeVisible();
  await page.getByRole('link', { name: p.signIn }).click();
  await page.waitForURL(/\/sign-in\?next=%2Fauctions%2F[0-9a-f-]{36}%2Fpay$/);
  await page.getByLabel('Email').fill(winner.email);
  await page.getByLabel('Password').fill(winner.password);
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await page.waitForURL(`**/auctions/${id}/pay`);
  const pay = page.getByRole('button', { name: p.pay('₹9,500') });
  await expect(pay).toBeVisible();
  await expect(page.locator('body')).toHaveCSS('background-color', 'rgb(13, 20, 36)');
  expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);

  // an order (routed), but checkout.js does not load: checked first, before anything loaded it
  const START = `**/api/v1/auctions/${id}/payment`;
  const ORDER = { payment_id: 'p', settlement_id: 's', status: 'created', amount: 950000, currency: 'INR', gateway: 'razorpay', key_id: 'rzp_test_stub', order_id: 'order_Stub123' };
  await page.route(START, (route) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(ORDER) }));
  await page.route(CHECKOUT_SRC, (route) => route.abort());
  await pay.click();
  await expect(page.getByText(p.scriptFailed)).toBeVisible();
  await page.unroute(CHECKOUT_SRC); // drops every route for it, the stand-in too: put that back
  await stubCheckout(page); // the loader tries afresh on the next Pay
  await page.unroute(START);

  // refused starts, routed: each has its own message, and nothing reaches the gateway
  const refuse = (status: number, code: string) => (route: import('@playwright/test').Route) =>
    route.fulfill({ status, contentType: 'application/json', body: JSON.stringify({ error: { code, message: code } }) });
  for (const [status, code, text] of [
    [409, 'AMOUNT_BELOW_GATEWAY_MINIMUM', enIN.errors.AMOUNT_BELOW_GATEWAY_MINIMUM],
    [503, 'SERVICE_UNAVAILABLE', p.unavailable],
  ] as const) {
    await page.route(START, refuse(status, code));
    await pay.click();
    await expect(page.getByText(text)).toBeVisible();
    await page.unroute(START);
  }
  await page.route(START, (route) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(ORDER) }));

  // keyboard: the focused Pay opens the window with Enter
  await pay.focus();
  await page.keyboard.press('Enter');
  await expect.poll(() => opened(page)).toBe(1);
  await expect(page.getByText(p.open)).toBeVisible();
  await ctx.close();

  // the seller of an auction that did not sell has nothing to pay
  const s = await signedIn(browser, seller, 390);
  await s.page.goto(`/auctions/${unsold}/pay`);
  await expect(s.page.getByText(p.noSale)).toBeVisible();
  await s.ctx.close();
  expect(violations).toEqual([]);
});
