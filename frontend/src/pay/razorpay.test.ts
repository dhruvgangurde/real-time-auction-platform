// Razorpay Checkout on the payment document (DC-10, SI-3): checkout.js is loaded once and only
// when the viewer chooses to pay; the ids it hands back are checked against the server's patterns
// before they are posted; the options carry the order, never an amount the client chose.
import { describe, expect, test } from 'vitest';

import { CHECKOUT_SRC, checkoutIds, checkoutOptions, createLoader } from './razorpay';

function fakeDom() {
  const appended: { src: string; onload: (() => void) | null; onerror: (() => void) | null }[] = [];
  const win: { Razorpay?: unknown } = {};
  const doc = {
    createElement: () => ({ src: '', async: false, onload: null, onerror: null }),
    head: { appendChild: (el: (typeof appended)[number]) => appended.push(el) },
  };
  return { appended, win, doc };
}

describe('the loader', () => {
  test('one script tag from checkout.razorpay.com, shared by every caller', async () => {
    const { appended, win, doc } = fakeDom();
    const load = createLoader(doc as never, win as never);
    const a = load();
    const b = load();
    expect(appended).toHaveLength(1);
    expect(appended[0]!.src).toBe(CHECKOUT_SRC);
    class Razorpay {}
    win.Razorpay = Razorpay;
    appended[0]!.onload?.();
    expect(await a).toBe(Razorpay);
    expect(await b).toBe(Razorpay);
  });

  test('a script that fails to load rejects, and the next try adds a new tag', async () => {
    const { appended, win, doc } = fakeDom();
    const load = createLoader(doc as never, win as never);
    const first = load();
    appended[0]!.onerror?.();
    await expect(first).rejects.toThrow();
    void load();
    expect(appended).toHaveLength(2);
  });
});

test('the ids handed back are checked like the server checks them (schemas/payments.py)', () => {
  const ok = { razorpay_order_id: 'order_Abc123', razorpay_payment_id: 'pay_Xyz789', razorpay_signature: 'f'.repeat(64) };
  expect(checkoutIds({ ...ok, extra: 'x' })).toEqual(ok);
  expect(checkoutIds({ ...ok, razorpay_signature: 'F'.repeat(64) })).toBeNull();
  expect(checkoutIds({ ...ok, razorpay_payment_id: 'pay_' })).toBeNull();
  expect(checkoutIds({ ...ok, razorpay_order_id: 'ord_1' })).toBeNull();
  expect(checkoutIds(null)).toBeNull();
});

test('the options carry the server order: key, order id and its amount, in rupees as paise', () => {
  const order = { payment_id: 'p1', settlement_id: 's1', status: 'created', amount: 950000, currency: 'INR', gateway: 'razorpay', key_id: 'rzp_test_X', order_id: 'order_Abc123' };
  const handler = () => undefined;
  const ondismiss = () => undefined;
  const options = checkoutOptions(order, { name: 'Real-Time Auction', description: 'Pentax K1000', color: '#162036', handler, ondismiss });
  expect(options).toEqual({
    key: 'rzp_test_X',
    order_id: 'order_Abc123',
    amount: 950000,
    currency: 'INR',
    name: 'Real-Time Auction',
    description: 'Pentax K1000',
    theme: { color: '#162036' },
    handler,
    modal: { ondismiss, escape: true },
  });
  expect(options).not.toHaveProperty('prefill'); // no personal data handed to the gateway
});
