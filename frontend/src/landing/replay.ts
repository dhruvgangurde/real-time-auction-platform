// The hero's replay (brief 8.1 "Demo off or paused"; slice 5 plan, Q2): the lot of the most recent
// verified stress run, played back from its ledger at the recorded spacing. Every row appears at
// its own Redis decision time (placed_at_ms), so rows decided in one atomic step appear together;
// nothing is compressed, interpolated or invented. Pure, so the timing is tested without a clock.
import type { HistoryBid } from '../lib/contract';

/** a stress run is at most 50 bidders x 20 bids; the loader stops at ten pages of 100 */
export const LEDGER_CAP = 1_000;
export const SHOWN = 6; // the hero ledger's rows (brief 8.1)
export const ANIMATED = 5; // a frame that applies several bids animates at most five (8.1)
const LEAD_IN_MS = 1_000; // before the first loaded bid, when the opening itself is not loaded

export type ReplayBid = { seq: number; amount: number; alias: string; auto: boolean; at: number };

export type Recording = {
  /** oldest first; every one has its decision time */
  bids: ReplayBid[];
  /** bids before the first one here: not loaded, or not placeable; shown as one honest row */
  earlier: number;
  startMs: number;
  /** the lot's final end: the replay closes here */
  endMs: number;
};

export type Frame = {
  /** how many bids have been applied */
  applied: number;
  latest: ReplayBid | null;
  /** newest first */
  rows: ReplayBid[];
  earlierRow: number | null;
  remainingMs: number;
  closed: boolean;
};

/** The recording from the ledger's pages (each newest first, as GET /bids answers) and the lot. */
export function record(pages: HistoryBid[][], lot: { start_time_ms: number; end_time_ms: number }): Recording {
  const bySeq = new Map<number, HistoryBid>();
  for (const page of pages) for (const b of page) bySeq.set(b.sequence_no, b);
  let rows = [...bySeq.values()].sort((a, b) => a.sequence_no - b.sequence_no);
  // a row with no decision time cannot be placed in time; neither can anything before it
  let untimed = -1;
  rows.forEach((b, i) => {
    if (b.placed_at_ms === null) untimed = i;
  });
  if (untimed !== -1) rows = rows.slice(untimed + 1);
  if (rows.length > LEDGER_CAP) rows = rows.slice(-LEDGER_CAP);
  const bids = rows.map((b) => ({ seq: b.sequence_no, amount: b.amount, alias: b.bidder_alias, auto: b.bid_type === 'automatic', at: b.placed_at_ms! }));
  const earlier = bids.length > 0 ? bids[0]!.seq - 1 : 0;
  const startMs = earlier > 0 ? bids[0]!.at - LEAD_IN_MS : lot.start_time_ms;
  return { bids, earlier, startMs, endMs: lot.end_time_ms };
}

/** How many bids are due at replay time t: decision times only grow with the sequence. */
function due(bids: ReplayBid[], t: number): number {
  let lo = 0;
  let hi = bids.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (bids[mid]!.at <= t) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

/** The board at replay time t. */
export function frameAt(rec: Recording, t: number, shown = SHOWN): Frame {
  const applied = due(rec.bids, t);
  const rows = rec.bids.slice(Math.max(0, applied - shown), applied).reverse();
  return {
    applied,
    latest: applied > 0 ? rec.bids[applied - 1]! : null,
    rows,
    earlierRow: rec.earlier > 0 && rows.length < shown ? rec.earlier : null,
    remainingMs: Math.max(0, rec.endMs - t),
    closed: t >= rec.endMs,
  };
}

/** The rows a frame added since the previous one, and how many of them animate. */
export function freshRows(prevApplied: number, applied: number): { fresh: number; animated: number } {
  const fresh = Math.max(0, applied - prevApplied);
  return { fresh, animated: Math.min(ANIMATED, fresh) };
}

export type Playhead = { elapsedMs: number; lastMs: number | null };

/** The replay's own clock: it runs only while playing (in view, tab visible), so time away from
 * the board is never skipped over. */
export function advance(p: Playhead, nowMs: number, playing: boolean): Playhead {
  if (!playing) return { elapsedMs: p.elapsedMs, lastMs: null };
  return { elapsedMs: p.elapsedMs + (p.lastMs === null ? 0 : nowMs - p.lastMs), lastMs: nowMs };
}
