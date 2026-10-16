// The seller's dashboard groups (FR-19, brief 9.8). The server has no status filter (gap S1), so
// these are groups of the pages loaded so far, each newest first as the server sent them.
import type { AuctionCard, AuctionStatus } from '../lib/contract';

export type SectionKey = 'draft' | 'scheduled' | 'live' | 'closed' | 'cancelled';

const SECTION: Record<AuctionStatus, SectionKey> = {
  draft: 'draft',
  scheduled: 'scheduled',
  live: 'live',
  closing: 'live',
  closed_sold: 'closed',
  closed_no_sale: 'closed',
  cancelled: 'cancelled',
};
const ORDER: SectionKey[] = ['draft', 'scheduled', 'live', 'closed', 'cancelled'];

export function sections(items: readonly AuctionCard[]): { key: SectionKey; items: AuctionCard[] }[] {
  return ORDER.map((key) => ({ key, items: items.filter((c) => SECTION[c.status] === key) })).filter((s) => s.items.length > 0);
}
