import { expect, test } from 'vitest';

import type { Settlement, UserProfile } from '../lib/contract';
import { initialRoom, type RoomState } from '../live/room';
import { blockedBy, isDemoSale, paidAt } from './account';

const profile = (status: string): UserProfile => ({ id: 'u', email: 'a@example.com', full_name: 'A', avatar_url: null, status, roles: [] });
const refused = (code: string): RoomState => ({ ...initialRoom('a1'), last: { kind: 'rejected', code, minNext: null, what: 'bid' } });
const settlement = (over: Partial<Settlement>): Settlement => ({
  id: 's', auction_id: 'a', status: 'pending_payment', final_price: 950000, winner: { id: 'u', full_name: 'Asha' },
  created_at_ms: 1, paid_at_ms: null, payment: null, ...over,
});

test('suspended or not verified, from the profile or a refused bid, replaces the bid and maximum controls', () => {
  expect(blockedBy(initialRoom('a1'), profile('active'))).toBeNull();
  expect(blockedBy(initialRoom('a1'), undefined)).toBeNull();
  expect(blockedBy(initialRoom('a1'), profile('suspended'))).toBe('suspended');
  expect(blockedBy(refused('ACCOUNT_SUSPENDED'), profile('active'))).toBe('suspended'); // profile still cached active
  expect(blockedBy(initialRoom('a1'), profile('pending_verification'))).toBe('unverified');
  expect(blockedBy(refused('EMAIL_NOT_VERIFIED'), undefined)).toBe('unverified');
  expect(blockedBy(refused('BID_TOO_LOW'), profile('active'))).toBeNull();
});

test("a won room shows Pay until the settlement reads paid, then when it was paid (FR-68, FR-69)", () => {
  expect(paidAt(undefined)).toBeNull(); // not read yet, or the read failed: Pay, and the payment page decides
  expect(paidAt(settlement({}))).toBeNull();
  expect(paidAt(settlement({ status: 'paid', paid_at_ms: 99 }))).toBe(99);
  expect(paidAt(settlement({ status: 'demo' }))).toBeNull();
});

test('a demo sale is never payable: the room offers no Pay for it (B2)', () => {
  expect(isDemoSale(settlement({ status: 'demo' }))).toBe(true);
  expect(isDemoSale(settlement({}))).toBe(false);
  expect(isDemoSale(settlement({ status: 'paid', paid_at_ms: 99 }))).toBe(false);
  expect(isDemoSale(undefined)).toBe(false); // not read yet: Pay, and the payment page decides
});
