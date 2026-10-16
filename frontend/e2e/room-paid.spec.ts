// The won room after payment (FR-68, FR-69; the slice 3 leftover): Pay until the settlement reads
// paid, then "Paid on <time>" and the receipt link; a room opened after the win does not announce
// it again. The payment is real on the backend: POST /payment creates a sandbox order and a signed
// webhook captures it, so this skips when the api container cannot reach api.razorpay.com (the
// beforeAll pre-check, and only that; E2E_RAZORPAY_CHECK_URL points it elsewhere, see
// e2e/checkout-stub.spec.ts).
import { expect, test } from '@playwright/test';

import { enIN } from '../src/strings/en-IN';
import { apiReachesRazorpay, newPaymentId, sendWebhook } from './fixtures/pay';
import { API, bearer, newAccount, newAuction, restBid, signInUi, waitForStatus } from './fixtures/stack';

const GATEWAY = 'skipped: the api container cannot reach api.razorpay.com, which creates the order (beforeAll pre-check)';
const b = enIN.roomUi.bid;
let reachable = false;
test.beforeAll(() => {
  reachable = apiReachesRazorpay();
});

test("a paid lot's room says Paid with the receipt instead of Pay; a won room opened later announces nothing", async ({ page, request }) => {
  test.skip(!reachable, GATEWAY);
  test.setTimeout(150_000);
  const seller = await newAccount(request, 'seller', 'seller');
  const winner = await newAccount(request, 'winner');
  const id = await newAuction(request, seller, { startInS: 2, endInS: 25, thresholdS: 10, extensionS: 10, reservePrice: null });
  await waitForStatus(request, id, 'live');
  await (await restBid(request, winner, id, 950000)).json();
  await waitForStatus(request, id, 'closed_sold', 60_000);

  const assertive = page.locator('#live-assertive');
  await signInUi(page, winner);
  await page.goto(`/auctions/${id}`);
  await expect(page.getByRole('link', { name: b.pay('₹9,500') })).toBeVisible();
  await page.waitForTimeout(1_500); // the first snapshot has been applied
  await expect(assertive).toHaveText(''); // the win was not happening now: nothing to say

  // the payment, as Razorpay would complete it: an order from the server, then a signed capture
  const order = (await (await request.post(`${API}/auctions/${id}/payment`, { headers: bearer(winner) })).json()) as { order_id: string };
  expect(await sendWebhook(request, 'payment.captured', { id: newPaymentId(), order_id: order.order_id, amount: 950000 })).toBe(200);
  await expect
    .poll(async () => ((await (await request.get(`${API}/auctions/${id}/settlement`, { headers: bearer(winner) })).json()) as { status: string }).status)
    .toBe('paid');

  await page.reload();
  await expect(page.getByText(/^Paid on /)).toBeVisible();
  await expect(page.getByRole('link', { name: b.receipt })).toHaveAttribute('href', `/auctions/${id}/pay`);
  await expect(page.getByRole('link', { name: b.pay('₹9,500') })).toHaveCount(0);
  await page.waitForTimeout(1_500);
  await expect(assertive).toHaveText('');
});
