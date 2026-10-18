// The payment page as a state machine (brief 9.5; FR-65 to FR-69, DC-10): who may pay, the window
// opening, closing, failing inside, the confirmation and the settlement read that alone says paid.
import { describe, expect, test } from 'vitest';

import type { Settlement } from '../lib/contract';
import { payReducer, type PayEvent, type PayState } from './flow';

const ME = 'u-winner';
const settlement = (over: Partial<Settlement> = {}): Settlement => ({
  id: 's1', auction_id: 'a1', status: 'pending_payment', final_price: 950000, winner: { id: ME, full_name: 'Bob' },
  created_at_ms: 1, paid_at_ms: null, payment: null, ...over,
});
const run = (events: PayEvent[], from: PayState = { kind: 'loading' }) => events.reduce(payReducer, from);
const loaded = (s: Settlement, viewerId = ME): PayEvent => ({ type: 'loaded', settlement: s, viewerId });

describe('who sees what (FR-65, FR-69)', () => {
  test('the winner of an unpaid settlement can pay; a failed last attempt is said', () => {
    expect(run([loaded(settlement())])).toEqual({ kind: 'due', amount: 950000, lastFailed: false, notice: null, retryAfterMs: null });
    const failed = settlement({ payment: { id: 'p', status: 'failed', amount: 950000, currency: 'INR', order_id: 'order_A', payment_id: 'pay_B', updated_at_ms: 2 } });
    expect(run([loaded(failed)])).toMatchObject({ kind: 'due', lastFailed: true });
  });

  test('a paid settlement is the receipt; the seller (or an admin) sees the status only', () => {
    const paid = settlement({ status: 'paid', paid_at_ms: 99, payment: { id: 'p', status: 'succeeded', amount: 950000, currency: 'INR', order_id: 'order_A', payment_id: 'pay_B', updated_at_ms: 99 } });
    expect(run([loaded(paid)])).toEqual({ kind: 'paid', amount: 950000, paidAtMs: 99, paymentId: 'pay_B' });
    expect(run([loaded(paid, 'u-seller')])).toEqual({ kind: 'status', settlement: paid });
    expect(run([loaded(settlement(), 'u-seller')])).toMatchObject({ kind: 'status' });
  });

  test('a demo lot is never paid: winner, seller and admin all see "Demo lot: nothing to pay" (B2 Q1)', () => {
    const demo = settlement({ status: 'demo' });
    expect(run([loaded(demo)])).toEqual({ kind: 'demo' });
    expect(run([loaded(demo, 'u-seller')])).toEqual({ kind: 'demo' });
    expect(run([loaded(demo), { type: 'pay' }])).toEqual({ kind: 'demo' }); // no Pay to press
  });

  test('a no-sale settlement has nothing to pay', () => {
    expect(run([loaded(settlement({ status: 'no_sale', final_price: null, winner: null }), 'u-seller')])).toEqual({ kind: 'noSale' });
  });

  test('a refused read: not the winner, not found; a session gone; anything else can be retried', () => {
    expect(run([{ type: 'loadFailed', code: 'FORBIDDEN' }])).toEqual({ kind: 'refused', code: 'FORBIDDEN' });
    expect(run([{ type: 'loadFailed', code: 'NOT_FOUND' }])).toEqual({ kind: 'refused', code: 'NOT_FOUND' });
    expect(run([{ type: 'loadFailed', code: 'INVALID_TOKEN' }])).toEqual({ kind: 'signedOut' });
    expect(run([{ type: 'loadFailed', code: 'SERVICE_UNAVAILABLE' }])).toEqual({ kind: 'error', code: 'SERVICE_UNAVAILABLE' });
  });
});

describe('paying', () => {
  const due = run([loaded(settlement())]);

  test('pay, the window opens, and closing it without paying comes back to due with a note', () => {
    expect(run([{ type: 'pay' }], due)).toEqual({ kind: 'opening', amount: 950000 });
    expect(run([{ type: 'pay' }, { type: 'opened' }], due)).toEqual({ kind: 'open', amount: 950000, failedInWindow: false });
    expect(run([{ type: 'pay' }, { type: 'opened' }, { type: 'dismissed' }], due)).toEqual({
      kind: 'due', amount: 950000, lastFailed: false, notice: 'DISMISSED', retryAfterMs: null,
    });
  });

  test('a failure inside the window, then closing it, is Failed; Try again opens it again', () => {
    const failed = run([{ type: 'pay' }, { type: 'opened' }, { type: 'gatewayFailed' }, { type: 'dismissed' }], due);
    expect(failed).toEqual({ kind: 'failed', amount: 950000 });
    expect(run([{ type: 'pay' }], failed)).toEqual({ kind: 'opening', amount: 950000 });
  });

  test('a refused start says why; already paid reads the settlement again; the script failing is its own note', () => {
    const opening = run([{ type: 'pay' }], due);
    expect(run([{ type: 'startFailed', code: 'SERVICE_UNAVAILABLE' }], opening)).toMatchObject({ kind: 'due', notice: 'SERVICE_UNAVAILABLE' });
    expect(run([{ type: 'startFailed', code: 'RATE_LIMITED', retryAfterMs: 30_000 }], opening)).toMatchObject({ notice: 'RATE_LIMITED', retryAfterMs: 30_000 });
    expect(run([{ type: 'startFailed', code: 'ALREADY_PAID' }], opening)).toEqual({ kind: 'loading' });
    expect(run([{ type: 'startFailed', code: 'NOT_WINNER' }], opening)).toEqual({ kind: 'refused', code: 'NOT_WINNER' });
    expect(run([{ type: 'scriptFailed' }], opening)).toMatchObject({ kind: 'due', notice: 'SCRIPT' });
  });

  test('below the gateway minimum is for good: no Pay, and Pay does nothing; unavailable keeps its retry', () => {
    const opening = run([{ type: 'pay' }], due);
    const below = run([{ type: 'startFailed', code: 'AMOUNT_BELOW_GATEWAY_MINIMUM' }], opening);
    expect(below).toEqual({ kind: 'belowMinimum', amount: 950000 });
    expect(run([{ type: 'pay' }], below)).toEqual(below);
    const unavailable = run([{ type: 'startFailed', code: 'SERVICE_UNAVAILABLE' }], opening);
    expect(unavailable).toEqual({ kind: 'due', amount: 950000, lastFailed: false, notice: 'SERVICE_UNAVAILABLE', retryAfterMs: null });
    expect(run([{ type: 'pay' }], unavailable)).toEqual({ kind: 'opening', amount: 950000 });
  });
});

describe('confirming: only the server says paid', () => {
  const confirming = run([loaded(settlement()), { type: 'pay' }, { type: 'opened' }, { type: 'authorized' }]);

  test('the checkout handing back ids is Confirming, never Paid', () => {
    expect(confirming).toEqual({ kind: 'confirming', amount: 950000, unverified: false });
    expect(run([{ type: 'confirmed', status: 'paid' }], confirming)).toEqual(confirming);
    expect(run([{ type: 'confirmed', status: 'pending' }], confirming)).toEqual(confirming);
  });

  test('paid only when the settlement read says paid (the callback, the webhook or the reconciler)', () => {
    const stillDue = settlement();
    expect(run([{ type: 'polled', settlement: stillDue }], confirming)).toEqual(confirming);
    const paid = settlement({ status: 'paid', paid_at_ms: 5, payment: { id: 'p', status: 'succeeded', amount: 950000, currency: 'INR', order_id: 'order_A', payment_id: 'pay_C', updated_at_ms: 5 } });
    expect(run([{ type: 'polled', settlement: paid }], confirming)).toEqual({ kind: 'paid', amount: 950000, paidAtMs: 5, paymentId: 'pay_C' });
  });

  test('the server answering failed is Failed; an unverifiable confirmation keeps confirming, said so', () => {
    expect(run([{ type: 'confirmed', status: 'failed' }], confirming)).toEqual({ kind: 'failed', amount: 950000 });
    expect(run([{ type: 'confirmFailed', code: 'INVALID_SIGNATURE' }], confirming)).toEqual({ ...confirming, unverified: true });
    expect(run([{ type: 'confirmFailed', code: 'PAYMENT_MISMATCH' }], confirming)).toEqual({ ...confirming, unverified: true });
    expect(run([{ type: 'confirmFailed', code: 'RATE_LIMITED' }], confirming)).toEqual(confirming); // the webhook still settles it
  });

  test('events that do not belong to a state change nothing', () => {
    const due = run([loaded(settlement())]);
    for (const e of [{ type: 'opened' }, { type: 'dismissed' }, { type: 'authorized' }, { type: 'confirmed', status: 'paid' }] as PayEvent[]) {
      expect(run([e], due), e.type).toEqual(due);
    }
  });
});
