// The marigold band (brief 6) starts only when a sequence higher than the last one this page saw
// arrives while the room is live; staleness, a pending bid or a re-render never restart it, and a
// first snapshot (a fresh page load) never starts one.
import { expect, test } from 'vitest';

import type { InboundFrame, LiveSnapshot } from '../lib/contract';
import { initialRoom, roomReducer, type RoomAction, type RoomState } from '../live/room';
import { NO_BAND, nextBand, type Band } from './band';

const A = 'a1';
const T = 1_000_000;

const snapshot = (seq: number, over: Partial<LiveSnapshot> = {}): LiveSnapshot => ({
  auction_id: A, status: 'live', current_price: 900000 + seq * 50000, leader_alias: 'Bidder-0001', sequence_no: seq, bid_count: seq,
  start_price: 900000, min_increment: 50000, min_next: 950000 + seq * 50000, end_time_ms: T + 600_000, extension_count: 0,
  participants: 2, recent_bids: [], degraded: false, reserve_met: null, self_outbid_blocked: true, server_time_ms: T, viewer: null, ...over,
});
const placed = (seq: number): InboundFrame => ({
  type: 'bid_placed', auction_id: A, sequence_no: seq, amount: 900000 + seq * 50000, bidder_alias: `Bidder-${seq}`, bid_type: 'manual',
  end_time_ms: T + 600_000, extended: false, extension_count: 0, placed_at_ms: T + seq, reserve_met: null, server_time_ms: T + seq,
});
const frame = (f: InboundFrame): RoomAction => ({ type: 'frame', frame: f });

/** Apply actions one by one, folding the band over every intermediate state (one per render). */
function play(start: RoomState, band: Band, ...actions: RoomAction[]): { state: RoomState; bands: Band[] } {
  let state = start;
  const bands: Band[] = [];
  for (const action of actions) {
    state = roomReducer(state, action).state;
    band = nextBand(band, state);
    bands.push(band);
  }
  return { state, bands };
}

const subscribed = (seq: number, over: Partial<LiveSnapshot> = {}): RoomAction => ({ type: 'snapshot', source: 'subscribed', body: snapshot(seq, over), yourAlias: 'Bidder-you' });

test('a first snapshot never starts the band (a fresh page load)', () => {
  expect(nextBand(NO_BAND, initialRoom(A))).toBe(NO_BAND); // no snapshot yet
  const { bands } = play(initialRoom(A), NO_BAND, { type: 'connection', state: 'connected' }, subscribed(2));
  expect(bands.at(-1)).toEqual({ seen: 2, seq: null });
  const closed = play(initialRoom(A), NO_BAND, subscribed(2, { status: 'closed_sold' })).bands.at(-1);
  expect(closed?.seq).toBeNull();
});

test('a new higher sequence starts it; stale, reconnect, pending and a refused bid at the same sequence do not', () => {
  const ready = play(initialRoom(A), NO_BAND, { type: 'connection', state: 'connected' }, subscribed(2));
  const { state, bands } = play(
    ready.state,
    ready.bands.at(-1) as Band,
    frame(placed(3)), // a new bid: the band starts on row 3
    { type: 'connection', state: 'reconnecting' }, // stale
    { type: 'connection', state: 'reconnected' },
    { type: 'subscribing' },
    subscribed(3), // back, same sequence
    { type: 'bidSent', requestId: 'r1', amount: 1050000, kind: 'bid' }, // pending
    frame({ type: 'bid_rejected', request_id: 'r1', auction_id: A, code: 'BID_TOO_LOW', message: 'low', min_next: 1100000, server_time_ms: T }),
  );
  const started = bands[0];
  expect(started).toEqual({ seen: 3, seq: 3 });
  for (const later of bands.slice(1)) expect(later).toBe(started); // the very same band: never restarted
  // the next higher sequence starts a new one
  expect(play(state, started as Band, frame(placed(4))).bands[0]).toEqual({ seen: 4, seq: 4 });
});

test('a higher sequence that arrives while stale or terminal is seen but starts no band', () => {
  const ready = play(initialRoom(A), NO_BAND, { type: 'connection', state: 'connected' }, subscribed(2));
  const degraded = play(ready.state, ready.bands.at(-1) as Band, { type: 'snapshot', source: 'fetch', body: snapshot(3, { degraded: true }) });
  expect(degraded.bands.at(-1)).toEqual({ seen: 3, seq: null });
  const closed = play(ready.state, ready.bands.at(-1) as Band, { type: 'snapshot', source: 'fetch', body: snapshot(3, { status: 'closed_sold' }) });
  expect(closed.bands.at(-1)).toEqual({ seen: 3, seq: null });
});
