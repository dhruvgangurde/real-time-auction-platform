// The replay card's wording: the present tense while the replay plays, the outcome once it has
// reached the close (brief 8.1).
import { expect, test } from 'vitest';

import { cardWords } from './cardWords';

const bid = { alias: 'Bidder-3f2a', seq: 4 };

test('while the replay plays: Current bid, and who leads with which bid', () => {
  expect(cardWords(bid, false, true, '₹9,000')).toEqual({
    label: 'Current bid',
    lead: 'Bidder-3f2a leads with bid №4.',
  });
  expect(cardWords(null, false, true, '₹9,000')).toEqual({
    label: 'Starting price',
    lead: 'No bids yet. The first bid can be ₹9,000.',
  });
});

test('once it has reached the close: Final bid, and who won, or no sale', () => {
  expect(cardWords(bid, true, true, '₹9,000')).toEqual({
    label: 'Final bid',
    lead: 'Bidder-3f2a won with bid №4.',
  });
  expect(cardWords(bid, true, false, '₹9,000')).toEqual({ label: 'Final bid', lead: 'Closed without a sale.' });
  expect(cardWords(null, true, false, '₹9,000')).toEqual({ label: 'Starting price', lead: 'Closed without a sale.' });
});
