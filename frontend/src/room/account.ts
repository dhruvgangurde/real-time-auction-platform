// The account states that replace the bid control (brief 9.3): suspended or not verified, known
// from the profile or from a bid the server refused for that reason. The maximum control goes
// with the bid control, so both read this one selector.
import type { Settlement, UserProfile } from '../lib/contract';
import type { RoomState } from '../live/room';

export type Blocked = 'suspended' | 'unverified' | null;

export function blockedBy(state: RoomState, profile: UserProfile | undefined): Blocked {
  const code = state.last?.kind === 'rejected' ? state.last.code : null;
  if (profile?.status === 'suspended' || code === 'ACCOUNT_SUSPENDED') return 'suspended';
  if (profile?.status === 'pending_verification' || code === 'EMAIL_NOT_VERIFIED') return 'unverified';
  return null;
}

/** A demo lot's sale is never payable (B2): the room offers no Pay for it. */
export function isDemoSale(settlement: Settlement | undefined): boolean {
  return settlement?.status === 'demo';
}

/** When the won lot was paid, or null: Pay stays until the settlement itself says paid (FR-68). */
export function paidAt(settlement: Settlement | undefined): number | null {
  return settlement?.status === 'paid' ? settlement.paid_at_ms : null;
}
