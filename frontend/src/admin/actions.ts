// What an administrator may do from the room (FR-63, BR-9; services/admin.py): force-close a live
// or closing auction, cancel a draft, scheduled or live one. The server still decides.
import type { AuctionStatus } from '../lib/contract';

export function roomActions(status: AuctionStatus): { forceClose: boolean; cancel: boolean } {
  return {
    forceClose: status === 'live' || status === 'closing',
    cancel: status === 'draft' || status === 'scheduled' || status === 'live',
  };
}
