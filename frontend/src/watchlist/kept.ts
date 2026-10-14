// What the watchlist page shows: the server's list, plus anything removed during this visit (it
// stays, unpressed, so "Watch again" undoes the removal), in the server's order: ending soonest
// first, then id (repositories/watchlist.py).
import type { AuctionCard } from '../lib/contract';

export function kept(shown: readonly AuctionCard[], fresh: readonly AuctionCard[]): AuctionCard[] {
  const ids = new Set(fresh.map((c) => c.id));
  return [...fresh, ...shown.filter((c) => !ids.has(c.id))].sort((a, b) => a.end_time_ms - b.end_time_ms || a.id.localeCompare(b.id));
}
