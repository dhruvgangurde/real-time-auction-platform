// What a listing card says (brief 9.2) and when the list asks the server again. Prices and end
// times come from the list response; the time left is the end time against the server clock, a
// local tick only repaints it (UI-4). A live card past its end says Closing until a refetch.
import type { AuctionCard, AuctionStatus } from '../lib/contract';

export type CardView =
  | { kind: 'draft'; price: number; startsAtMs: number }
  | { kind: 'scheduled'; price: number; startsAtMs: number }
  | { kind: 'live'; price: number; hasBids: boolean; bids: number; endTimeMs: number; remainingMs: number }
  | { kind: 'closing'; price: number; hasBids: boolean; bids: number }
  | { kind: 'sold'; price: number }
  | { kind: 'noSale' }
  | { kind: 'cancelled' };

export function cardView(c: AuctionCard, nowMs: number): CardView {
  const price = c.current_price ?? c.starting_price;
  const bids = { price, hasBids: c.current_price !== null, bids: c.bid_count };
  switch (c.status) {
    case 'live':
      return c.end_time_ms > nowMs
        ? { kind: 'live', ...bids, endTimeMs: c.end_time_ms, remainingMs: c.end_time_ms - nowMs }
        : { kind: 'closing', ...bids };
    case 'closing':
      return { kind: 'closing', ...bids };
    case 'closed_sold':
      return { kind: 'sold', price };
    case 'closed_no_sale':
      return { kind: 'noSale' };
    case 'cancelled':
      return { kind: 'cancelled' };
    case 'draft': // only on its seller's dashboard (FR-30)
      return { kind: 'draft', price: c.starting_price, startsAtMs: c.start_time_ms };
    default:
      return { kind: 'scheduled', price: c.starting_price, startsAtMs: c.start_time_ms };
  }
}

const FINISHED = new Set<AuctionStatus>(['closed_sold', 'closed_no_sale', 'cancelled']);

/** Whether a card shows the Watch toggle: a lot still to be decided does; a finished one (sold,
 * not sold, cancelled) only while watched, as "Watching", so it can be removed. */
export function cardWatch(status: AuctionStatus, watched: boolean): boolean {
  return watched || !FINISHED.has(status);
}

/** A live card's end passed between two ticks: time to ask the server what happened. */
export function crossedZero(cards: readonly AuctionCard[], prevMs: number, nowMs: number): boolean {
  return cards.some((c) => c.status === 'live' && c.end_time_ms > prevMs && c.end_time_ms <= nowMs);
}

export const POLL_MS = 15_000;

/** The list refetches every 15 s while the tab is visible, and stops once more than three pages
 * are loaded (every poll refetches every page); the zero-crossing refetch still runs. */
export function pollInterval(pagesLoaded: number): number | false {
  return pagesLoaded > 3 ? false : POLL_MS;
}
