// What an administrator may do to an account (brief 9.9; FR-75, FR-76; services/users.py), offered
// only where the API allows it. The server still refuses the last active administrator (LAST_ADMIN)
// and the administrator's own account (CANNOT_TARGET_SELF). Every account has the bidder role from
// registration, so only seller and admin are offered.
import type { AdminUser } from './api';

export type PersonAction = { kind: 'suspend' } | { kind: 'reinstate' } | { kind: 'grant' | 'revoke'; role: 'seller' | 'admin' };

export function userActions(person: AdminUser, viewerId: string | undefined): PersonAction[] {
  const self = person.id === viewerId;
  const out: PersonAction[] = [];
  if (person.status === 'suspended') out.push({ kind: 'reinstate' });
  else if (!self) out.push({ kind: 'suspend' });
  for (const role of ['seller', 'admin'] as const) {
    if (!person.roles.includes(role)) out.push({ kind: 'grant', role });
    else if (!(self && role === 'admin')) out.push({ kind: 'revoke', role });
  }
  return out;
}
