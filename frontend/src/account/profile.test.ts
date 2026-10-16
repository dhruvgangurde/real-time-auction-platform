// The account page (FR-9, brief 9.1, 9.10): the links it offers by role (the header has room for
// icons only at 390, so a seller's listings and the administration are reached from here too), and
// what the API would refuse about a name (1 to 200 characters; it does not trim, so this does).
import { expect, test } from 'vitest';

import { accountLinks, nameProblem } from './profile';

test('every account: its bids and its email choices; a seller its listings; an administrator the administration', () => {
  expect(accountLinks(['bidder'])).toEqual(['bids', 'email']);
  expect(accountLinks(['bidder', 'seller'])).toEqual(['bids', 'listings', 'email']);
  expect(accountLinks(['bidder', 'seller', 'admin'])).toEqual(['bids', 'listings', 'admin', 'email']);
});

test('a name: not blank, at most 200 characters', () => {
  expect(nameProblem('Asha Rao')).toBeNull();
  expect(nameProblem('   ')).toBe('required');
  expect(nameProblem('x'.repeat(201))).toBe('long');
  expect(nameProblem(` ${'x'.repeat(200)} `)).toBeNull();
});
