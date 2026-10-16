// What an administrator may do to an account (FR-76, FR-75; services/users.py): suspend anyone but
// themselves while not suspended, reinstate a suspended account, give or take the seller and admin
// roles, never their own admin role. The server still refuses LAST_ADMIN and CANNOT_TARGET_SELF.
import { expect, test } from 'vitest';

import type { AdminUser } from './api';
import { userActions } from './people';

const person = (over: Partial<AdminUser> = {}): AdminUser => ({
  id: 'u1', email: 'asha@example.com', full_name: 'Asha Rao', status: 'active', roles: ['bidder'], created_at_ms: 1, ...over,
});

test('an active bidder: suspend, and give either role', () => {
  expect(userActions(person(), 'me')).toEqual([{ kind: 'suspend' }, { kind: 'grant', role: 'seller' }, { kind: 'grant', role: 'admin' }]);
});

test('a suspended seller and admin: reinstate, and take either role', () => {
  expect(userActions(person({ status: 'suspended', roles: ['bidder', 'seller', 'admin'] }), 'me')).toEqual([
    { kind: 'reinstate' },
    { kind: 'revoke', role: 'seller' },
    { kind: 'revoke', role: 'admin' },
  ]);
});

test('oneself: no suspend and no removing the own admin role', () => {
  expect(userActions(person({ id: 'me', roles: ['bidder', 'seller', 'admin'] }), 'me')).toEqual([{ kind: 'revoke', role: 'seller' }]);
});
