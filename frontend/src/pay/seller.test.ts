// What the seller reads on the payment page (FR-64, FR-69, BR-10): who won and at what price, and
// whether the payment has arrived. Never the buyer's attempts: a failed try is the buyer's business.
// The winner's name is the seller's to see once a settlement exists (BR-10), and never starts a line.
import { describe, expect, test } from 'vitest';

import type { Settlement } from '../lib/contract';
import { payEnIN } from '../strings/pay-en-IN';
import { sellerLines } from './seller';

const at = (ms: number) => `t${ms}`;
const settlement = (over: Partial<Settlement> = {}): Settlement => ({
  id: 's1', auction_id: 'a1', status: 'pending_payment', final_price: 950000, winner: { id: 'u1', full_name: 'Asha Rao' },
  created_at_ms: 1, paid_at_ms: null, payment: null, ...over,
});
const failedAttempt = { id: 'p', status: 'failed', amount: 950000, currency: 'INR', order_id: 'order_A', payment_id: 'pay_B', updated_at_ms: 2 };

describe('the seller’s copy', () => {
  test('awaiting: who won and that the payment has not arrived, with or without failed attempts', () => {
    for (const payment of [null, failedAttempt]) {
      expect(sellerLines(settlement({ payment }), payEnIN, at)).toEqual(['Won by Asha Rao at ₹9,500.', 'The payment has not arrived yet.']);
    }
  });

  test('paid: who won and when it was paid', () => {
    const paid = settlement({ status: 'paid', paid_at_ms: 99, payment: { ...failedAttempt, status: 'succeeded' } });
    expect(sellerLines(paid, payEnIN, at)).toEqual(['Won by Asha Rao at ₹9,500.', 'Paid on t99']);
  });

  test('no line mentions an attempt, and none starts with the winner’s name', () => {
    for (const s of [settlement({ payment: failedAttempt }), settlement({ status: 'paid', paid_at_ms: 9 })]) {
      for (const line of sellerLines(s, payEnIN, at)) {
        expect(line).not.toMatch(/attempt|did not go through|fail/i);
        expect(line.startsWith('Asha')).toBe(false);
      }
    }
  });
});
