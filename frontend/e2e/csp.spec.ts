// brief 13 items 24 and 25: the payment document carries its own CSP, the only one that allows
// Razorpay, and every other response keeps today's policy byte for byte (NFR-21). Both are
// pinned here; a change to either is a decision (docs/decisions.md), never a side effect.
import { expect, test } from '@playwright/test';

const TODAY =
  "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; connect-src 'self' ws://localhost:8011; font-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'";
const PAY =
  "default-src 'self'; script-src 'self' https://checkout.razorpay.com https://cdn.razorpay.com; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self' https://lumberjack.razorpay.com https://lumberjack-metrics.razorpay.com; frame-src https://api.razorpay.com; font-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'";

const ID = '00000000-0000-0000-0000-000000000000';
// documents and responses that are not the payment page, including near misses of its path
const OTHERS = [
  '/',
  '/auctions',
  `/auctions/${ID}`,
  `/auctions/${ID}/bids`,
  '/watchlist',
  '/notifications',
  '/sign-in',
  '/register',
  '/help',
  '/nothing-here',
  `/auctions/${ID}/pay/`,
  `/auctions/${ID}/payment`,
  '/auctions/not-a-uuid/pay',
  '/pay.html',
  '/api/v1/health/live',
];

test('the payment document has its own policy, exactly, with the referrer and sniffing headers', async ({ request }) => {
  for (const path of [`/auctions/${ID}/pay`, `/auctions/${ID}/pay?from=room`]) {
    const r = await request.get(path);
    expect(r.status(), path).toBe(200);
    expect(r.headers()['content-security-policy'], path).toBe(PAY);
    expect(r.headers()['referrer-policy'], path).toBe('no-referrer');
    expect(r.headers()['x-content-type-options'], path).toBe('nosniff');
    expect(await r.text(), path).toContain('/assets/pay-');
  }
});

test('every other document and response keeps today’s policy byte for byte', async ({ request }) => {
  for (const path of OTHERS) {
    const r = await request.get(path);
    expect(r.headers()['content-security-policy'], path).toBe(TODAY);
    expect(r.headers()['referrer-policy'], path).toBe('no-referrer');
  }
});
