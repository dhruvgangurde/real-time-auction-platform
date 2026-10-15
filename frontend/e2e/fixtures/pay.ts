// Checkout test helpers (FR-65 to FR-69): a stand-in for Razorpay's checkout.js served at its own
// URL (so the payment page's enforced CSP still applies), the checkout callback's signature and a
// signed webhook as Razorpay would send them. The two secrets are read from the api container's
// environment when needed and kept in memory only: never printed, logged or written to disk.
import { execFileSync } from 'node:child_process';
import { createHmac, randomBytes } from 'node:crypto';

import type { APIRequestContext, Page } from '@playwright/test';

import { API, ROOT } from './stack';

export const CHECKOUT_SRC = 'https://checkout.razorpay.com/v1/checkout.js';

/** The stand-in records its options and listeners; a test drives it from page.evaluate. */
const STUB = `window.Razorpay = class {
  constructor(options) { this.options = options; this.failed = []; window.__rzp = this; }
  on(event, listener) { if (event === 'payment.failed') this.failed.push(listener); }
  open() { window.__rzpOpened = (window.__rzpOpened || 0) + 1; }
};`;

export async function stubCheckout(page: Page): Promise<void> {
  await page.route(CHECKOUT_SRC, (route) => route.fulfill({ contentType: 'application/javascript', body: STUB }));
}

/** Every CSP violation on our own documents, across navigations (full loads included). */
export async function watchViolations(page: Page): Promise<string[]> {
  const seen: string[] = [];
  await page.exposeFunction('__cspViolation', (line: string) => seen.push(line));
  await page.addInitScript(() => {
    if (window.top !== window) return;
    const report = (window as unknown as { __cspViolation: (line: string) => void }).__cspViolation;
    document.addEventListener('securitypolicyviolation', (e) => report(`${location.pathname} ${e.effectiveDirective} ${e.blockedURI}`));
  });
  return seen;
}

/** The beforeAll pre-check: can the api container reach api.razorpay.com (any HTTP answer)?
 * E2E_RAZORPAY_CHECK_URL, for tests only, points this check (and nothing else) at another URL,
 * such as a host that cannot answer, to prove the skip branch. It goes to Python as an argument. */
export function apiReachesRazorpay(url = process.env.E2E_RAZORPAY_CHECK_URL || 'https://api.razorpay.com/v1/'): boolean {
  try {
    execFileSync(
      'docker',
      ['compose', 'exec', '-T', 'api', 'python', '-c', 'import sys, httpx; httpx.get(sys.argv[1], timeout=8)', url],
      { cwd: ROOT, stdio: 'ignore', timeout: 30_000 },
    );
    return true;
  } catch {
    return false;
  }
}

function secret(name: 'RAZORPAY_KEY_SECRET' | 'RAZORPAY_WEBHOOK_SECRET'): string {
  const value = execFileSync('docker', ['compose', 'exec', '-T', 'api', 'printenv', name], { cwd: ROOT, stdio: ['ignore', 'pipe', 'ignore'] })
    .toString()
    .trim();
  if (value === '') throw new Error(`${name} is not set in the api container`);
  return value;
}

export const newPaymentId = () => `pay_${randomBytes(9).toString('hex').slice(0, 14)}`;

/** What checkout.js hands the success handler: its signature binds the payment to our order. */
export function checkoutIds(orderId: string, paymentId: string) {
  const signature = createHmac('sha256', secret('RAZORPAY_KEY_SECRET')).update(`${orderId}|${paymentId}`).digest('hex');
  return { razorpay_order_id: orderId, razorpay_payment_id: paymentId, razorpay_signature: signature };
}

/** A webhook as Razorpay sends it, signed over the raw body. */
export async function sendWebhook(
  request: APIRequestContext,
  event: 'payment.captured' | 'payment.failed',
  entity: { id: string; order_id: string; amount: number },
): Promise<number> {
  const body = JSON.stringify({
    entity: 'event',
    event,
    payload: {
      payment: {
        entity: { ...entity, currency: 'INR', status: event === 'payment.captured' ? 'captured' : 'failed', ...(event === 'payment.failed' ? { error_code: 'BAD_REQUEST_ERROR' } : {}) },
      },
    },
  });
  const signature = createHmac('sha256', secret('RAZORPAY_WEBHOOK_SECRET')).update(body).digest('hex');
  const r = await request.post(`${API}/payments/razorpay/webhook`, {
    headers: { 'content-type': 'application/json', 'x-razorpay-signature': signature, 'x-razorpay-event-id': `evt_${randomBytes(8).toString('hex')}` },
    data: body,
  });
  return r.status();
}
