// The real Razorpay sandbox (SI-3, DC-10, brief 13 item 25): checkout.js and its window as
// Razorpay serves them, under the payment page's enforced CSP. Runs only with
// E2E_RAZORPAY_SANDBOX=1 (needs the internet in the browser and the api container, and test keys);
// the default suite never talks to Razorpay from the browser. 1280: a full Netbanking test payment
// to the receipt, with no CSP violation on our document and no payment detail reaching our API.
// 390: the window opens full screen (its phone layout comes from its injected <style>).
import { expect, test } from '@playwright/test';

import { payEnIN as p } from '../src/strings/pay-en-IN';
import { watchViolations } from './fixtures/pay';
import { newAccount, newAuction, restBid, signInUi, waitForStatus } from './fixtures/stack';

const MOBILE = '9123456780'; // a test number Razorpay accepts (it refuses obvious dummies)

test.skip(process.env.E2E_RAZORPAY_SANDBOX !== '1', 'skipped: set E2E_RAZORPAY_SANDBOX=1 to drive the Razorpay sandbox');

async function won(request: import('@playwright/test').APIRequestContext) {
  const seller = await newAccount(request, 'seller', 'seller');
  const winner = await newAccount(request, 'winner');
  const id = await newAuction(request, seller, { startInS: 2, endInS: 20, thresholdS: 10, extensionS: 10, reservePrice: null });
  await waitForStatus(request, id, 'live');
  await (await restBid(request, winner, id, 900000)).json();
  await waitForStatus(request, id, 'closed_sold', 60_000);
  return { id, winner };
}

test('1280: a Netbanking test payment through the real window ends on the receipt, with no violation and no payment detail sent to us', async ({ browser, request }) => {
  test.setTimeout(240_000);
  const { id, winner } = await won(request);
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page = await ctx.newPage();
  const violations = await watchViolations(page);
  const toUs: string[] = [];
  ctx.on('request', (r) => r.url().startsWith('http://localhost:8011/api/') && toUs.push(`${new URL(r.url()).pathname} ${r.postData() ?? ''}`));
  await signInUi(page, winner);
  await page.goto(`/auctions/${id}/pay`);
  await page.getByRole('button', { name: p.pay('₹9,000') }).click();

  const frame = page.frameLocator('iframe.razorpay-checkout-frame');
  const contact = frame.locator('input[name=contact]');
  await contact.click();
  await contact.pressSequentially(MOBILE, { delay: 50 });
  await frame.getByRole('button', { name: 'Continue' }).last().click();
  await frame.getByText('Netbanking', { exact: true }).click();
  const bank = page.waitForEvent('popup', { timeout: 30_000 });
  await frame.getByText('Bank of Baroda - Retail Banking', { exact: true }).first().click();
  const popup = await bank;
  await popup.waitForLoadState();
  await popup.getByRole('button', { name: /success/i }).click();

  await expect(page.getByRole('heading', { name: p.paidTitle })).toBeVisible({ timeout: 90_000 });
  await expect(page.getByText(p.paid('₹9,000'))).toBeVisible();
  expect(violations).toEqual([]);
  const leaks = toUs.filter((line) => line.includes(MOBILE) || /card|cvv|bank|vpa|upi/i.test(line));
  expect(leaks).toEqual([]); // DC-10: Razorpay's window keeps the payment details
  await ctx.close();
});

test('390: the window opens full screen, with no violation', async ({ browser, request }) => {
  test.setTimeout(180_000);
  const { id, winner } = await won(request);
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 } });
  const page = await ctx.newPage();
  const violations = await watchViolations(page);
  await signInUi(page, winner);
  await page.goto(`/auctions/${id}/pay`);
  await page.getByRole('button', { name: p.pay('₹9,000') }).click();
  const frame = page.locator('iframe.razorpay-checkout-frame');
  await expect(frame.contentFrame().locator('input[name=contact]')).toBeVisible({ timeout: 30_000 });
  expect(await frame.boundingBox()).toEqual({ x: 0, y: 0, width: 390, height: 844 });
  expect(violations).toEqual([]);
  await ctx.close();
});
