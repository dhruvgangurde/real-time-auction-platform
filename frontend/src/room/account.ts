// The account states that replace the bid control (brief 9.3): suspended or not verified, known
// from the profile or from a bid the server refused for that reason. The maximum control goes
// with the bid control, so both read this one selector.
import type { UserProfile } from '../lib/contract';
import type { RoomState } from '../live/room';

export type Blocked = 'suspended' | 'unverified' | null;

export function blockedBy(state: RoomState, profile: UserProfile | undefined): Blocked {
  const code = state.last?.kind === 'rejected' ? state.last.code : null;
  if (profile?.status === 'suspended' || code === 'ACCOUNT_SUSPENDED') return 'suspended';
  if (profile?.status === 'pending_verification' || code === 'EMAIL_NOT_VERIFIED') return 'unverified';
  return null;
}
