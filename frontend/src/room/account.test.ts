import { expect, test } from 'vitest';

import type { UserProfile } from '../lib/contract';
import { initialRoom, type RoomState } from '../live/room';
import { blockedBy } from './account';

const profile = (status: string): UserProfile => ({ id: 'u', email: 'a@example.com', full_name: 'A', avatar_url: null, status, roles: [] });
const refused = (code: string): RoomState => ({ ...initialRoom('a1'), last: { kind: 'rejected', code, minNext: null, what: 'bid' } });

test('suspended or not verified, from the profile or a refused bid, replaces the bid and maximum controls', () => {
  expect(blockedBy(initialRoom('a1'), profile('active'))).toBeNull();
  expect(blockedBy(initialRoom('a1'), undefined)).toBeNull();
  expect(blockedBy(initialRoom('a1'), profile('suspended'))).toBe('suspended');
  expect(blockedBy(refused('ACCOUNT_SUSPENDED'), profile('active'))).toBe('suspended'); // profile still cached active
  expect(blockedBy(initialRoom('a1'), profile('pending_verification'))).toBe('unverified');
  expect(blockedBy(refused('EMAIL_NOT_VERIFIED'), undefined)).toBe('unverified');
  expect(blockedBy(refused('BID_TOO_LOW'), profile('active'))).toBeNull();
});
