// A closed listing's result for its seller (brief 9.8; FR-64, FR-69, BR-10): sold is one line,
// "Won by <name> at ₹X.", then the payment status, never a separate "Sold for"; not sold says so
// and, with a reserve, that it was not met.
import { expect, test } from 'vitest';

import type { Settlement } from '../lib/contract';
import { resultLines } from './result';

const at = (ms: number) => `t${ms}`;
const settlement = (over: Partial<Settlement> = {}): Settlement => ({
  id: 's', auction_id: 'a', status: 'pending_payment', final_price: 950000, winner: { id: 'u', full_name: 'Asha Rao' },
  created_at_ms: 1, paid_at_ms: null, payment: null, ...over,
});

test('sold: who won at what price, then whether the payment arrived; nothing else', () => {
  expect(resultLines('closed_sold', null, settlement(), at)).toEqual(['Won by Asha Rao at ₹9,500.', 'The payment has not arrived yet.']);
  expect(resultLines('closed_sold', true, settlement({ status: 'paid', paid_at_ms: 7 }), at)).toEqual(['Won by Asha Rao at ₹9,500.', 'Paid on t7']);
  for (const line of resultLines('closed_sold', null, settlement(), at)) expect(line).not.toMatch(/^Sold for/);
});

test('not sold, with the reserve not met when there was one', () => {
  expect(resultLines('closed_no_sale', null, undefined, at)).toEqual(['Not sold.']);
  expect(resultLines('closed_no_sale', false, undefined, at)).toEqual(['Not sold. Bidding did not reach your reserve.']);
});
