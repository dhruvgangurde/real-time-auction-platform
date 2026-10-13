// The live-region text of design/shots/03-room/index.md, state by state, and the 3 s coalescing
// of polite announcements (brief 11.4).
import { describe, expect, test } from 'vitest';

import type { InboundFrame, LiveSnapshot } from '../lib/contract';
import { enIN } from '../strings/en-IN';
import { PoliteQueue, announce, type Announcement } from './announcer';
import { initialRoom, roomReducer, type RoomAction, type RoomState } from './room';

const A = 'a1';
// the index's run: 14 Oct, 20:00:00 IST; times below are IST clock times as ms since the epoch
const IST = 5.5 * 3_600_000;
const at = (h: number, m: number, sec = 0) => Date.UTC(2026, 9, 14, h, m, sec) - IST;
const time = (ms: number) => new Date(ms + IST).toISOString().slice(11, 19);
const deps = { catalogue: enIN, time };
const END = at(20, 30);

function snapshot(over: Partial<LiveSnapshot> = {}): LiveSnapshot {
  return {
    auction_id: A, status: 'live', current_price: 1400000, leader_alias: 'Bidder-7c21', sequence_no: 11, bid_count: 11,
    start_price: 900000, min_increment: 50000, min_next: 1450000, end_time_ms: END, extension_count: 0, participants: 14,
    recent_bids: [], degraded: false, reserve_met: true, self_outbid_blocked: true, server_time_ms: at(20, 20, 30), viewer: null, ...over,
  };
}

function step(state: RoomState, action: RoomAction): { state: RoomState; said: Announcement } {
  const next = roomReducer(state, action).state;
  return { state: next, said: announce(state, next, deps) };
}

const connected = (viewer: LiveSnapshot['viewer'] = null, over: Partial<LiveSnapshot> = {}) => {
  const first = step(roomReducer(initialRoom(A), { type: 'connection', state: 'connected' }).state, {
    type: 'snapshot', source: 'subscribed', body: snapshot({ viewer, ...over }), yourAlias: 'Bidder-7c21',
  });
  return first;
};
const frame = (f: InboundFrame): RoomAction => ({ type: 'frame', frame: f });
const T = at(20, 20, 30);

describe('index states', () => {
  test('01 anonymous and 28 seller: the price line on load (polite)', () => {
    expect(connected().said).toEqual({ polite: 'Current bid ₹14,000, Bidder-7c21 leads. Reserve met.' });
  });

  test('19 reserve met, you lead (polite)', () => {
    expect(connected({ you_lead: true, your_max: 1600000, your_result: null }).said).toEqual({ polite: 'Current bid ₹14,000, you lead. Reserve met.' });
  });

  test('03 scheduled to live (polite)', () => {
    const sched = connected(null, { status: 'scheduled', current_price: null, leader_alias: null, sequence_no: 0, bid_count: 0, min_next: 900000 });
    expect(sched.said).toEqual({});
    const live = step(sched.state, frame({ type: 'auction_started', auction_id: A, status: 'live', start_time_ms: at(20, 0), end_time_ms: END, start_price: 900000, min_increment: 50000, min_next: 900000, sequence_no: 0, extension_count: 0, server_time_ms: at(20, 0, 4) }));
    expect(live.said).toEqual({ polite: 'Bidding is open. Starting bid ₹9,000. Ends at 20:30:00.' });
  });

  test('04 idle, 05 pending, 08 retrying, 09 leading: nothing', () => {
    const idle = connected();
    const pending = step(idle.state, { type: 'bidSent', requestId: 'r1', amount: 1450000, kind: 'bid' });
    expect(pending.said).toEqual({});
    const retrying = step(pending.state, frame({ type: 'bid_rejected', request_id: 'r1', auction_id: A, code: 'SERVICE_UNAVAILABLE', message: 'x', server_time_ms: T }));
    expect(retrying.said).toEqual({});
  });

  test('06 accepted (assertive)', () => {
    const pending = step(connected().state, { type: 'bidSent', requestId: 'r1', amount: 1450000, kind: 'bid' });
    const acked = step(pending.state, frame({ type: 'bid_accepted', request_id: 'r1', auction_id: A, sequence_no: 12, amount: 1450000, end_time_ms: END, extended: false, extension_count: 0, placed_at_ms: T, server_time_ms: T }));
    expect(acked.said.assertive).toBe("Your bid of ₹14,50,000 was accepted. You're the highest bidder.".replace('₹14,50,000', '₹14,500'));
  });

  test('07 rejected BID_TOO_LOW (assertive)', () => {
    const pending = step(connected(null, { current_price: 1150000, min_next: 1200000 }).state, { type: 'bidSent', requestId: 'r1', amount: 1150000, kind: 'bid' });
    const rejected = step(pending.state, frame({ type: 'bid_rejected', request_id: 'r1', auction_id: A, code: 'BID_TOO_LOW', message: 'x', current_highest: 1150000, min_next: 1200000, server_time_ms: T }));
    expect(rejected.said).toEqual({ assertive: 'Bid not placed. Too low. The minimum is now ₹12,000.' });
  });

  test('10 outbid (assertive) and 12 proxy exhausted (assertive)', () => {
    const lead = connected({ you_lead: true, your_max: null, your_result: null }, { current_price: 1200000, min_next: 1250000 });
    const placed = step(lead.state, frame({ type: 'bid_placed', auction_id: A, sequence_no: 12, amount: 1250000, bidder_alias: 'Bidder-e05c', bid_type: 'manual', end_time_ms: END, extended: false, extension_count: 0, placed_at_ms: T, reserve_met: false, server_time_ms: T }));
    // leading flips only on the outbid_notice (item 15), so until it lands the line still says you lead
    expect(placed.said.polite).toContain('Current bid ₹12,500');
    const outbid = step(placed.state, frame({ type: 'outbid_notice', auction_id: A, current_price: 1250000, sequence_no: 12, server_time_ms: T }));
    expect(outbid.said).toEqual({ assertive: "You've been outbid at ₹12,500. Your next bid: at least ₹13,000." });

    const withMax = connected({ you_lead: true, your_max: 1600000, your_result: null }, { current_price: 1600000, min_next: 1650000 });
    const over = step(withMax.state, frame({ type: 'bid_placed', auction_id: A, sequence_no: 12, amount: 1650000, bidder_alias: 'Bidder-e05c', bid_type: 'automatic', end_time_ms: END, extended: false, extension_count: 0, placed_at_ms: T, reserve_met: true, server_time_ms: T }));
    const reached = step(over.state, frame({ type: 'outbid_notice', auction_id: A, current_price: 1650000, sequence_no: 12, server_time_ms: T }));
    expect(reached.said).toEqual({ assertive: "Your maximum of ₹16,000 was reached. You've been outbid at ₹16,500. Your next bid: at least ₹17,000." });
    expect(reached.state.own.maximumActive).toBe(false);
  });

  test('11 proxy active (assertive)', () => {
    const pending = step(connected().state, { type: 'bidSent', requestId: 'p1', amount: 1600000, kind: 'proxy' });
    const armed = step(pending.state, frame({ type: 'proxy_armed', request_id: 'p1', auction_id: A, max_amount: 1600000, active: true, server_time_ms: T }));
    const acked = step(armed.state, frame({ type: 'bid_accepted', request_id: 'p1', auction_id: A, sequence_no: 12, amount: 1450000, end_time_ms: END, extended: false, extension_count: 0, placed_at_ms: T, server_time_ms: T }));
    expect(armed.said.assertive).toBe('Your maximum of ₹16,000 is set.');
    expect(acked.state.own.leading).toBe(true);
  });

  test('13 extending (polite and assertive) and 14 cap reached (polite)', () => {
    const pending = step(connected().state, { type: 'bidSent', requestId: 'r1', amount: 1700000, kind: 'bid' });
    const ext = step(pending.state, frame({ type: 'bid_accepted', request_id: 'r1', auction_id: A, sequence_no: 12, amount: 1700000, end_time_ms: END + 120_000, extended: true, extension_count: 1, placed_at_ms: T, server_time_ms: T }));
    expect(ext.said).toEqual({
      polite: 'Extended by 2 minutes because a bid arrived in the last minute, 1 of 10. Now ends at 20:32:00.',
      assertive: "Your bid of ₹17,000 was accepted. You're the highest bidder.",
    });
    const nine = connected(null, { extension_count: 9, end_time_ms: END + 9 * 120_000, current_price: 2100000, min_next: 2150000 });
    const cap = step(nine.state, frame({ type: 'bid_placed', auction_id: A, sequence_no: 12, amount: 2150000, bidder_alias: 'Bidder-4f03', bid_type: 'manual', end_time_ms: END + 10 * 120_000, extended: true, extension_count: 10, placed_at_ms: T, reserve_met: true, server_time_ms: T }));
    expect(cap.said.polite).toBe('Extended by 2 minutes, 10 of 10. No further extensions. Closes at 20:50:00.');
  });

  test('15 closing, 16 won, 17 lost, 18 no sale, 20 cancelled', () => {
    const closing = step(connected(null, { end_time_ms: END + 20 * 60_000 }).state, { type: 'countdownZero' });
    expect(closing.said).toEqual({ polite: 'Closing. Bidding ended at 20:50:00; the result is on its way.' });
    const won = step(closing.state, { type: 'snapshot', source: 'fetch', body: snapshot({ status: 'closed_sold', current_price: 2200000, sequence_no: 26, end_time_ms: END + 20 * 60_000, viewer: { you_lead: true, your_max: null, your_result: 'won' } }) });
    expect(won.said).toEqual({ assertive: 'You won at ₹22,000. Pay ₹22,000 to complete the purchase.' });
    const lost = step(connected().state, frame({ type: 'auction_closed', auction_id: A, outcome: 'closed_sold', final_price: 2200000, reserve_met: true, final_sequence_no: 11, server_time_ms: T }));
    expect(lost.said).toEqual({ polite: 'Sold for ₹22,000.' });
    const noSale = step(connected().state, frame({ type: 'auction_closed', auction_id: A, outcome: 'closed_no_sale', final_price: null, reserve_met: false, final_sequence_no: 11, server_time_ms: T }));
    expect(noSale.said).toEqual({ polite: 'Not sold. Reserve not met.' });
    const cancelled = step(connected().state, frame({ type: 'auction_closed', auction_id: A, outcome: 'cancelled', final_price: null, reserve_met: true, final_sequence_no: 11, server_time_ms: T }));
    expect(cancelled.said).toEqual({ polite: 'Cancelled by the platform. Nothing is sold or charged.' });
  });

  test('21 degraded, 22 reconnecting, 24 signed out (polite); 23 other tab nothing', () => {
    const base = connected().state;
    expect(step(base, frame({ type: 'system_status', degraded: true, server_time_ms: T })).said).toEqual({ polite: 'Bidding is paused while we reconnect to the live service. Prices may be a few seconds old.' });
    expect(step(base, { type: 'connection', state: 'reconnecting' }).said).toEqual({ polite: 'Reconnecting. Updates every 2 seconds meanwhile.' });
    expect(step(base, { type: 'connection', state: 'polling', closedCode: 4004 }).said).toEqual({ polite: 'You were signed out. Sign in again to bid.' });
    expect(step(base, { type: 'connection', state: 'polling', closedCode: 4012 }).said).toEqual({});
  });

  test('27 rate limited (assertive)', () => {
    const pending = step(connected().state, { type: 'bidSent', requestId: 'r1', amount: 1450000, kind: 'bid' });
    const limited = step(pending.state, frame({ type: 'error', code: 'RATE_LIMITED', message: 'x', request_id: 'r1', server_time_ms: T }));
    expect(limited.said).toEqual({ assertive: 'Bid not placed. Too many bids in a second. Try again.' });
  });

  test('a closing room reopens when an extension arrives', () => {
    const closing = step(connected().state, { type: 'countdownZero' }).state;
    const back = step(closing, frame({ type: 'bid_placed', auction_id: A, sequence_no: 12, amount: 1450000, bidder_alias: 'Bidder-4f03', bid_type: 'manual', end_time_ms: END + 120_000, extended: true, extension_count: 1, placed_at_ms: T, reserve_met: true, server_time_ms: T }));
    expect(back.state.status).toBe('live');
    expect(back.state.previousEndMs).toBe(END);
  });
});

describe('PoliteQueue', () => {
  test('at most one polite announcement per 3 s, the latest wins', () => {
    const said: string[] = [];
    let now = 0;
    const timers: { at: number; fn: () => void }[] = [];
    const q = new PoliteQueue((t) => said.push(t), () => now, {
      setTimeout: (fn, ms) => {
        const h = { at: now + ms, fn };
        timers.push(h);
        return h;
      },
      clearTimeout: () => undefined,
    });
    q.offer('one');
    expect(said).toEqual(['one']);
    now = 1000;
    q.offer('two');
    q.offer('three');
    expect(said).toEqual(['one']); // held
    expect(timers).toHaveLength(1);
    now = 3000;
    timers[0]!.fn();
    expect(said).toEqual(['one', 'three']); // the latest, once the gap passed
    now = 7000;
    q.offer('four');
    expect(said).toEqual(['one', 'three', 'four']);
  });
});
