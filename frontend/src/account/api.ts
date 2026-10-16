// The account's calls (FR-9, FR-10) on the shared client; they live in the account chunk.
import { parsed, type Api } from '../api/client';

export type MyBid = { id: number; auction_id: string; amount: number; sequence_no: number; bid_type: string; created_at_ms: number };
export type MyBidPage = { items: MyBid[]; next_cursor: string | null };

function bid(v: unknown): MyBid | null {
  const b = v as Partial<Record<'id' | 'auction_id' | 'amount' | 'sequence_no' | 'bid_type' | 'created_at', unknown>> | null;
  if (b === null || typeof b !== 'object') return null;
  const { id, auction_id, amount, sequence_no, bid_type, created_at } = b;
  const at = typeof created_at === 'string' ? Date.parse(created_at) : Number.NaN; // an ISO time here, not ms
  if (typeof id !== 'number' || typeof auction_id !== 'string' || typeof amount !== 'number' || typeof sequence_no !== 'number' || typeof bid_type !== 'string' || Number.isNaN(at)) return null;
  return { id, auction_id, amount, sequence_no, bid_type, created_at_ms: at };
}

function bidPage(v: unknown): MyBidPage | null {
  const p = v as { items?: unknown; next_cursor?: unknown } | null;
  if (p === null || !Array.isArray(p.items) || (p.next_cursor !== null && typeof p.next_cursor !== 'string')) return null;
  const items = p.items.map(bid);
  return items.every((x): x is MyBid => x !== null) ? { items, next_cursor: p.next_cursor } : null;
}

/** FR-10: the user's own ledger rows across all auctions, newest first; keyset on `cursor`. */
export async function myBids(api: Api, cursor?: string): Promise<MyBidPage> {
  const q = new URLSearchParams({ limit: '20' });
  if (cursor !== undefined) q.set('cursor', cursor);
  return parsed(await api.request('GET', `/users/me/bids?${q}`, { auth: 'required' }), bidPage);
}

/** FR-9: only the name and avatar are the user's to change; the avatar is not offered (gap C1). */
export async function updateName(api: Api, name: string): Promise<void> {
  await api.request('PATCH', '/users/me', { auth: 'required', body: { full_name: name } });
}
