// The hero's replay (brief 8.1 "Demo off or paused", slice 5 plan): a verified stress run's ledger
// played back at its recorded spacing, from its own Redis decision times. Nothing is invented: a
// quiet stretch stays quiet, the price is only ever an accepted amount, and bids that could not be
// loaded are one honest row, never made-up rows.
import { expect, test } from 'vitest';

import type { HistoryBid } from '../lib/contract';
import { advance, frameAt, freshRows, LEDGER_CAP, record, type Recording } from './replay';

const START = 1_800_000_000_000;
const lot = { start_time_ms: START, end_time_ms: START + 75_000 };

/** a ledger row as GET /auctions/{id}/bids answers it */
const row = (seq: number, at: number | null, alias = `Bidder-${seq}`, auto = false): HistoryBid => ({
  sequence_no: seq,
  amount: 500_000 + (seq - 1) * 5_000,
  bidder_alias: alias,
  bid_type: auto ? 'automatic' : 'manual',
  placed_at_ms: at,
});

// bids 1 to 6: two share a millisecond (a maximum answering a bid in the same atomic step), then a
// ten-second quiet stretch before bid 6
const ledger = [row(1, START + 2_000), row(2, START + 2_400), row(3, START + 3_000), row(4, START + 3_000, 'Bidder-max', true), row(5, START + 4_100), row(6, START + 14_100)];
const newestFirst = (rows: HistoryBid[]) => [...rows].reverse();

test('pages arrive newest first; the recording runs oldest first from the opening', () => {
  const rec = record([newestFirst(ledger.slice(3)), newestFirst(ledger.slice(0, 3))], lot);
  expect(rec.bids.map((b) => b.seq)).toEqual([1, 2, 3, 4, 5, 6]);
  expect(rec.earlier).toBe(0);
  expect(rec.startMs).toBe(START);
  expect(rec.endMs).toBe(START + 75_000);
  expect(rec.bids[3]).toEqual({ seq: 4, amount: 515_000, alias: 'Bidder-max', auto: true, at: START + 3_000 });
});

test('rows are due at their recorded decision time, a shared millisecond together', () => {
  const rec = record([newestFirst(ledger)], lot);
  expect(frameAt(rec, START + 1_999).rows).toEqual([]);
  expect(frameAt(rec, START + 2_000).rows.map((b) => b.seq)).toEqual([1]);
  expect(frameAt(rec, START + 2_999).rows.map((b) => b.seq)).toEqual([2, 1]);
  expect(frameAt(rec, START + 3_000).rows.map((b) => b.seq)).toEqual([4, 3, 2, 1]); // 3 and 4 at once
});

test('a quiet stretch stays quiet: nothing moves between two recorded bids', () => {
  const rec = record([newestFirst(ledger)], lot);
  for (const t of [START + 4_100, START + 9_000, START + 14_099]) {
    const frame = frameAt(rec, t);
    expect(frame.latest?.seq).toBe(5);
    expect(frame.latest?.amount).toBe(520_000);
  }
  expect(frameAt(rec, START + 14_100).latest?.seq).toBe(6);
});

test('the price is only ever an accepted amount, and before the first bid there is none', () => {
  const rec = record([newestFirst(ledger)], lot);
  const amounts = new Set(ledger.map((b) => b.amount));
  expect(frameAt(rec, START).latest).toBeNull();
  for (let t = START; t <= START + 20_000; t += 137) {
    const latest = frameAt(rec, t).latest;
    if (latest !== null) expect(amounts.has(latest.amount)).toBe(true);
  }
});

test('the clock is the recorded end minus the replay time, and the lot closes at that end', () => {
  const rec = record([newestFirst(ledger)], lot);
  expect(frameAt(rec, START + 10_000).remainingMs).toBe(65_000);
  expect(frameAt(rec, START + 74_999).closed).toBe(false);
  const end = frameAt(rec, START + 75_000);
  expect(end.closed).toBe(true);
  expect(end.remainingMs).toBe(0);
});

test('at most six rows, newest first', () => {
  const many = Array.from({ length: 20 }, (_, i) => row(i + 1, START + 1_000 * (i + 1)));
  const rec = record([newestFirst(many)], lot);
  expect(frameAt(rec, START + 30_000).rows.map((b) => b.seq)).toEqual([20, 19, 18, 17, 16, 15]);
  expect(frameAt(rec, START + 30_000, 3).rows.map((b) => b.seq)).toEqual([20, 19, 18]);
});

test('older bids that could not be loaded are one honest row, and the replay starts at the first loaded', () => {
  const rec = record([newestFirst([row(312, START + 40_000), row(313, START + 40_500), row(314, START + 41_000)])], lot);
  expect(rec.earlier).toBe(311);
  expect(rec.startMs).toBe(START + 39_000); // a second's lead-in, not the opening it cannot show
  expect(frameAt(rec, rec.startMs).earlierRow).toBe(311);
  expect(frameAt(rec, START + 41_000).rows.map((b) => b.seq)).toEqual([314, 313, 312]);
  expect(frameAt(rec, START + 41_000).earlierRow).toBe(311); // under the rows while they fit
  const full = record([newestFirst(Array.from({ length: 8 }, (_, i) => row(100 + i, START + 40_000 + i * 100)))], lot);
  expect(frameAt(full, START + 50_000).earlierRow).toBeNull(); // six rows fill the table
});

test('rows without a decision time cannot be placed: they and everything before them count as earlier', () => {
  const rec = record([newestFirst([row(1, START + 1_000), row(2, null), row(3, START + 3_000), row(4, START + 4_000)])], lot);
  expect(rec.bids.map((b) => b.seq)).toEqual([3, 4]);
  expect(rec.earlier).toBe(2);
});

test(`more than ${LEDGER_CAP} bids: the newest ${LEDGER_CAP} replay, the rest are the honest row`, () => {
  const many = Array.from({ length: LEDGER_CAP + 37 }, (_, i) => row(i + 1, START + 10 * (i + 1)));
  const rec = record([newestFirst(many)], lot);
  expect(rec.bids).toHaveLength(LEDGER_CAP);
  expect(rec.bids[0]!.seq).toBe(38);
  expect(rec.earlier).toBe(37);
});

test('a frame that applies several bids animates at most five, the newest last', () => {
  expect(freshRows(0, 1)).toEqual({ fresh: 1, animated: 1 });
  expect(freshRows(10, 13)).toEqual({ fresh: 3, animated: 3 });
  expect(freshRows(10, 22)).toEqual({ fresh: 12, animated: 5 });
  expect(freshRows(10, 10)).toEqual({ fresh: 0, animated: 0 });
  expect(freshRows(22, 0)).toEqual({ fresh: 0, animated: 0 }); // "Replay again" starts over quietly
});

test('the playhead runs only while playing: off screen or in a hidden tab it waits', () => {
  let p = advance({ elapsedMs: 0, lastMs: null }, 1_000, true);
  p = advance(p, 1_250, true);
  p = advance(p, 1_500, true);
  expect(p.elapsedMs).toBe(500);
  p = advance(p, 9_000, false); // scrolled away: the time away does not count
  p = advance(p, 20_000, false);
  expect(p.elapsedMs).toBe(500);
  p = advance(p, 30_000, true); // back in view: resumes from where it stopped
  p = advance(p, 30_250, true);
  expect(p.elapsedMs).toBe(750);
});

test('an empty ledger records nothing to play', () => {
  const rec: Recording = record([[]], lot);
  expect(rec.bids).toEqual([]);
  expect(frameAt(rec, START + 80_000)).toMatchObject({ latest: null, rows: [], closed: true });
});
