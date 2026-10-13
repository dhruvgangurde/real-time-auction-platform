// Brief section 13 items 5 to 19 and 27, and the B1 client rules, as reducer cases. The table in
// frontend/README.md maps each item to the test names here.
import { describe, expect, test } from 'vitest';

import type { InboundFrame, LiveSnapshot, RoomFrame } from '../lib/contract';
import { canBid, initialRoom, isStale, roomReducer, type RoomAction, type RoomEffect, type RoomState } from './room';

const A = 'a1';
const T = 1_000_000;
const END = T + 600_000;

function snapshot(over: Partial<LiveSnapshot> = {}): LiveSnapshot {
  return {
    auction_id: A, status: 'live', current_price: 1000000, leader_alias: 'Bidder-0001', sequence_no: 2, bid_count: 2,
    start_price: 900000, min_increment: 50000, min_next: 1050000, end_time_ms: END, extension_count: 0, participants: 3,
    recent_bids: [
      { sequence_no: 2, amount: 1000000, bidder_alias: 'Bidder-0001', bid_type: 'manual', placed_at_ms: T },
      { sequence_no: 1, amount: 950000, bidder_alias: 'Bidder-0002', bid_type: 'manual', placed_at_ms: T - 1 },
    ],
    degraded: false, reserve_met: false, self_outbid_blocked: true, server_time_ms: T, viewer: null, ...over,
  };
}

function placed(seq: number, over: Partial<Extract<RoomFrame, { type: 'bid_placed' }>> = {}): InboundFrame {
  return {
    type: 'bid_placed', auction_id: A, sequence_no: seq, amount: 900000 + seq * 50000, bidder_alias: `Bidder-${seq}`,
    bid_type: 'manual', end_time_ms: END, extended: false, extension_count: 0, placed_at_ms: T + seq, reserve_met: false,
    server_time_ms: T + seq, ...over,
  };
}

function extended(seq: number, over: Partial<Extract<RoomFrame, { type: 'timer_extended' }>> = {}): InboundFrame {
  return { type: 'timer_extended', auction_id: A, sequence_no: seq, end_time_ms: END + 120_000, extension_count: 1, final: false, server_time_ms: T, ...over };
}

function presence(seq: number | null, participants = 3): InboundFrame {
  return { type: 'presence', auction_id: A, participants, sequence_no: seq, end_time_ms: END, server_time_ms: T };
}

function closed(final_sequence_no: number, outcome: 'closed_sold' | 'closed_no_sale' | 'cancelled' = 'closed_sold'): InboundFrame {
  return { type: 'auction_closed', auction_id: A, outcome, final_price: outcome === 'closed_sold' ? 1100000 : null, reserve_met: outcome === 'closed_sold', final_sequence_no, server_time_ms: T };
}

function accepted(seq: number, requestId = 'r1'): InboundFrame {
  return { type: 'bid_accepted', request_id: requestId, auction_id: A, sequence_no: seq, amount: 900000 + seq * 50000, end_time_ms: END, extended: false, extension_count: 0, placed_at_ms: T + seq, server_time_ms: T + seq };
}

/** Run actions in order; return the final state and every effect in order. */
function run(start: RoomState, ...actions: RoomAction[]): { state: RoomState; effects: RoomEffect[] } {
  let state = start;
  const effects: RoomEffect[] = [];
  for (const action of actions) {
    const step = roomReducer(state, action);
    state = step.state;
    effects.push(...step.effects);
  }
  return { state, effects };
}

const frame = (f: InboundFrame): RoomAction => ({ type: 'frame', frame: f });
const subscribedAt = (seq = 2, over: Partial<LiveSnapshot> = {}): RoomAction => ({ type: 'snapshot', source: 'subscribed', body: snapshot({ sequence_no: seq, ...over }), yourAlias: 'Bidder-you' });
const connected = (): RoomState => run(initialRoom(A), { type: 'connection', state: 'connected' }, subscribedAt()).state;

describe('item 5: connection and staleness', () => {
  test('a room is stale unless connected and subscribed', () => {
    const fresh = initialRoom(A);
    expect(isStale(fresh)).toBe(true);
    const sub = run(fresh, { type: 'connection', state: 'connected' }, subscribedAt()).state;
    expect(isStale(sub)).toBe(false);
    expect(isStale(run(sub, { type: 'connection', state: 'reconnecting' }).state)).toBe(true);
    expect(isStale(run(sub, { type: 'connection', state: 'reconnected' }).state)).toBe(false);
    expect(isStale(run(sub, { type: 'subscribing' }).state)).toBe(true);
    const polling = run(initialRoom(A, 'polling'), { type: 'snapshot', source: 'poll', body: snapshot() }).state;
    expect(isStale(polling)).toBe(false);
  });
});

describe('item 6: buffer until subscribed', () => {
  test('buffers events until subscribed, then applies only those above snapshot.sequence_no', () => {
    const { state, effects } = run(
      initialRoom(A),
      { type: 'connection', state: 'connected' },
      frame(placed(2)), frame(placed(3)), frame(placed(4)),
      subscribedAt(3),
    );
    expect(state.sequence).toBe(4);
    expect(state.price).toBe(900000 + 4 * 50000);
    expect(state.recentBids[0]?.sequence_no).toBe(4);
    expect(state.recentBids.map((b) => b.sequence_no)).toEqual([4, 2, 1]); // 3 came from the snapshot's list
    expect(effects).toEqual([]);
  });
});

describe('item 7: order, repeats, (type, sequence), final, gaps', () => {
  test('applies events in sequence order and drops repeats', () => {
    const { state } = run(connected(), frame(placed(3)), frame(placed(3)), frame(placed(2)), frame(placed(4)));
    expect(state.sequence).toBe(4);
    expect(state.recentBids.map((b) => b.sequence_no)).toEqual([4, 3, 2, 1]);
  });

  test('timer_extended sharing its bid sequence is applied once', () => {
    const { state } = run(connected(), frame(placed(3, { extended: true, extension_count: 1, end_time_ms: END + 120_000 })), frame(extended(3)), frame(extended(3)));
    expect(state.endTimeMs).toBe(END + 120_000);
    expect(state.extensionCount).toBe(1);
    expect(state.sequence).toBe(3);
  });

  test('no further extension from final or extension_count 10 on either event', () => {
    const byFlag = run(connected(), frame(placed(3)), frame(extended(3, { final: true, extension_count: 10, end_time_ms: END + 1_200_000 })));
    expect(byFlag.state.finalExtension).toBe(true);
    const byCount = run(connected(), frame(placed(3, { extension_count: 10, end_time_ms: END + 1_200_000 })));
    expect(byCount.state.finalExtension).toBe(true);
    expect(run(connected(), frame(placed(3))).state.finalExtension).toBe(false);
  });

  test('a gap waits 300 ms, then refetches', () => {
    const gap = run(connected(), frame(placed(5)));
    expect(gap.state.sequence).toBe(2);
    expect(gap.effects).toEqual([{ type: 'gapCheck', afterMs: 300 }]);
    // the missing events arrive in time: no refetch
    const filled = run(gap.state, frame(placed(3)), frame(placed(4)), { type: 'gapTimeout' });
    expect(filled.state.sequence).toBe(5);
    expect(filled.effects).toEqual([]);
    // or they do not: refetch
    const missing = run(gap.state, { type: 'gapTimeout' });
    expect(missing.effects).toEqual([{ type: 'refetch', reason: 'gap' }]);
    const after = run(missing.state, { type: 'snapshot', source: 'fetch', body: snapshot({ sequence_no: 5, current_price: 1150000 }) });
    expect(after.state.sequence).toBe(5);
    expect(after.state.buffer).toEqual([]);
  });
});

describe('item 8: presence and recovery refetch', () => {
  test('presence ahead of the last sequence refetches', () => {
    expect(run(connected(), frame(presence(2))).effects).toEqual([]);
    expect(run(connected(), frame(presence(3))).effects).toEqual([{ type: 'refetch', reason: 'presence-ahead' }]);
    expect(run(connected(), frame(presence(2, 9))).state.participants).toBe(9);
  });

  test('system_status degraded false refetches', () => {
    const down = run(connected(), frame({ type: 'system_status', degraded: true, server_time_ms: T }));
    expect(down.state.degraded).toBe(true);
    expect(down.effects).toEqual([]);
    const up = run(down.state, frame({ type: 'system_status', degraded: false, server_time_ms: T }));
    expect(up.state.degraded).toBe(false);
    expect(up.effects).toEqual([{ type: 'refetch', reason: 'recovered' }]);
  });
});

describe('item 9: auction_closed is always applied', () => {
  test('auction_closed is applied whatever its sequence', () => {
    const { state, effects } = run(connected(), frame(closed(2)));
    expect(state.terminal).toBe('closed_sold');
    expect(state.status).toBe('closed_sold');
    expect(state.finalPrice).toBe(1100000);
    expect(effects).toEqual([{ type: 'refetch', reason: 'closed' }]);
  });

  test('auction_closed first applies buffered bids up to final_sequence_no, refetching on a gap', () => {
    const inOrder = run(connected(), frame(placed(4)), frame(placed(3)), frame(closed(4)));
    expect(inOrder.state.sequence).toBe(4);
    expect(inOrder.state.recentBids[0]?.sequence_no).toBe(4);
    expect(inOrder.effects.filter((e) => e.type === 'refetch')).toEqual([{ type: 'refetch', reason: 'closed' }]);
    const withGap = run(connected(), frame(placed(4)), frame(closed(4)));
    expect(withGap.state.terminal).toBe('closed_sold');
    expect(withGap.effects.at(-1)).toEqual({ type: 'refetch', reason: 'closed-gap' });
  });

  test('a terminal /live status closes the room without auction_closed', () => {
    const { state } = run(connected(), { type: 'snapshot', source: 'fetch', body: snapshot({ status: 'closed_no_sale', sequence_no: 2 }) });
    expect(state.terminal).toBe('closed_no_sale');
  });
});

describe('item 10 and B1: terminal is sticky', () => {
  test('terminal status is sticky: later presence, snapshots or bids never reopen bidding', () => {
    const done = run(connected(), frame(closed(2))).state;
    const { state, effects } = run(done, frame(presence(7, 1)), frame(placed(3)), { type: 'snapshot', source: 'poll', body: snapshot({ status: 'live', sequence_no: 9 }) }, frame(extended(2)));
    expect(state.terminal).toBe('closed_sold');
    expect(state.status).toBe('closed_sold');
    expect(state.sequence).toBe(2);
    expect(canBid(state)).toBe(false);
    expect(effects.filter((e) => e.type === 'refetch')).toEqual([]);
  });
});

describe('item 11 and B1: scheduled rooms', () => {
  const scheduled = (): RoomState =>
    run(initialRoom(A), { type: 'connection', state: 'connected' }, subscribedAt(0, { status: 'scheduled', current_price: null, leader_alias: null, recent_bids: [], min_next: 900000 })).state;

  test('auction_started takes a scheduled room live and refetches', () => {
    const { state, effects } = run(scheduled(), frame({ type: 'auction_started', auction_id: A, status: 'live', start_time_ms: T, end_time_ms: END, start_price: 900000, min_increment: 50000, min_next: 900000, sequence_no: 0, extension_count: 0, server_time_ms: T }));
    expect(state.status).toBe('live');
    expect(canBid(state)).toBe(true);
    expect(effects).toEqual([{ type: 'refetch', reason: 'started' }]);
  });

  test('a /live snapshot saying live takes it live', () => {
    const { state } = run(scheduled(), { type: 'snapshot', source: 'fetch', body: snapshot({ sequence_no: 0, current_price: null, recent_bids: [] }) });
    expect(state.status).toBe('live');
  });

  test('the local clock passing start_time never enables bidding', () => {
    // nothing in the reducer reads a clock: with no server word the room stays scheduled
    const { state } = run(scheduled(), frame(presence(null, 4)), { type: 'connection', state: 'connected' });
    expect(state.status).toBe('scheduled');
    expect(canBid(state)).toBe(false);
  });

  test('a bid or presence frame in a scheduled room refetches /live', () => {
    expect(run(scheduled(), frame(placed(1))).effects).toEqual([{ type: 'refetch', reason: 'event-while-scheduled' }]);
    expect(run(scheduled(), frame(presence(1))).effects).toEqual([{ type: 'refetch', reason: 'presence-ahead' }]);
    expect(run(scheduled(), frame(presence(null))).effects).toEqual([]); // a quiet scheduled room
  });
});

describe('B1: polling never goes backwards', () => {
  test('a polled snapshot with a lower sequence_no or an earlier status is ignored', () => {
    const polling = run(initialRoom(A, 'polling'), { type: 'snapshot', source: 'poll', body: snapshot({ sequence_no: 5, current_price: 1150000 }) }).state;
    const older = run(polling, { type: 'snapshot', source: 'poll', body: snapshot({ sequence_no: 4, current_price: 1100000 }) }).state;
    expect(older.sequence).toBe(5);
    expect(older.price).toBe(1150000);
    const earlier = run(polling, { type: 'snapshot', source: 'poll', body: snapshot({ status: 'scheduled', sequence_no: 5 }) }).state;
    expect(earlier.status).toBe('live');
    const same = run(polling, { type: 'snapshot', source: 'poll', body: snapshot({ sequence_no: 5, participants: 8 }) }).state;
    expect(same.participants).toBe(8);
    const later = run(polling, { type: 'snapshot', source: 'poll', body: snapshot({ status: 'closing', sequence_no: 5 }) }).state;
    expect(later.status).toBe('closing');
  });
});

describe('items 13, 14, 16: own bids', () => {
  test('a sent bid is pending until its ack, then takes its sequence', () => {
    const sent = run(connected(), { type: 'bidSent', requestId: 'r1', amount: 1050000, kind: 'bid' });
    expect(sent.state.pending).toEqual({ requestId: 'r1', amount: 1050000, kind: 'bid' });
    expect(canBid(sent.state)).toBe(false);
    const acked = run(sent.state, frame(accepted(3, 'r1')));
    expect(acked.state.pending).toBeNull();
    expect(acked.state.own.sequences).toEqual([3]);
    expect(acked.state.own.leading).toBe(true);
    expect(acked.state.sequence).toBe(3); // the ack applied through the sequence rule (FR-40)
    expect(acked.state.recentBids[0]).toMatchObject({ sequence_no: 3, bidder_alias: 'Bidder-you' });
    // the broadcast of the same bid is then a repeat
    expect(run(acked.state, frame(placed(3))).state.recentBids.filter((b) => b.sequence_no === 3)).toHaveLength(1);
  });

  test('a rejection shows its code and min_next and clears the pending bid', () => {
    const sent = run(connected(), { type: 'bidSent', requestId: 'r1', amount: 1000000, kind: 'bid' });
    const { state } = run(sent.state, frame({ type: 'bid_rejected', request_id: 'r1', auction_id: A, code: 'BID_TOO_LOW', message: 'low', current_highest: 1050000, min_next: 1100000, server_time_ms: T }));
    expect(state.pending).toBeNull();
    expect(state.last).toEqual({ kind: 'rejected', code: 'BID_TOO_LOW', minNext: 1100000, what: 'bid' });
    expect(state.minNext).toBe(1100000);
    expect(canBid(state)).toBe(true);
  });

  test('a socket SERVICE_UNAVAILABLE keeps the bid pending and retries with the same request_id', () => {
    const sent = run(connected(), { type: 'bidSent', requestId: 'r1', amount: 1050000, kind: 'bid' });
    const { state, effects } = run(sent.state, frame({ type: 'bid_rejected', request_id: 'r1', auction_id: A, code: 'SERVICE_UNAVAILABLE', message: 'redis', server_time_ms: T }));
    expect(state.pending?.requestId).toBe('r1');
    expect(effects).toEqual([{ type: 'retryBid', requestId: 'r1' }]);
  });

  test('submit is disabled while a bid is pending', () => {
    const sent = run(connected(), { type: 'bidSent', requestId: 'r1', amount: 1050000, kind: 'bid' });
    const second = run(sent.state, { type: 'bidSent', requestId: 'r2', amount: 1100000, kind: 'bid' });
    expect(second.state.pending?.requestId).toBe('r1');
  });

  test('RATE_LIMITED disables submit until rateLimitOver', () => {
    const sent = run(connected(), { type: 'bidSent', requestId: 'r1', amount: 1050000, kind: 'bid' });
    const limited = run(sent.state, frame({ type: 'error', code: 'RATE_LIMITED', message: 'slow', request_id: 'r1', server_time_ms: T }));
    expect(limited.state.rateLimited).toBe(true);
    expect(canBid(limited.state)).toBe(false);
    expect(limited.effects).toEqual([{ type: 'rateLimitTimer', ms: 1000 }]);
    expect(canBid(run(limited.state, { type: 'rateLimitOver' }).state)).toBe(true);
  });
});

describe('item 15 and B1: own status never from alias equality', () => {
  test('leading comes from own acks, outbid_notice and you_lead only', () => {
    const lead = run(connected(), { type: 'bidSent', requestId: 'r1', amount: 1050000, kind: 'bid' }, frame(accepted(3, 'r1'))).state;
    expect(lead.own.leading).toBe(true);
    const outbid = run(lead, frame(placed(4)), frame({ type: 'outbid_notice', auction_id: A, current_price: 1100000, sequence_no: 4, server_time_ms: T })).state;
    expect(outbid.own.leading).toBe(false);
    expect(outbid.own.outbidAt).toBe(1100000);
    const again = run(outbid, { type: 'snapshot', source: 'fetch', body: snapshot({ sequence_no: 4, viewer: { you_lead: true, your_max: null, your_result: null } }) }).state;
    expect(again.own.leading).toBe(true);
  });

  test('you_lead and your_result from a fresh snapshot set leading, won and lost', () => {
    const won = run(connected(), { type: 'snapshot', source: 'fetch', body: snapshot({ status: 'closed_sold', viewer: { you_lead: true, your_max: null, your_result: 'won' } }) }).state;
    expect(won.own.result).toBe('won');
    const lost = run(connected(), { type: 'snapshot', source: 'fetch', body: snapshot({ status: 'closed_sold', viewer: { you_lead: false, your_max: null, your_result: 'lost' } }) }).state;
    expect(lost.own.result).toBe('lost');
  });

  test('an alias match labels a row You but never makes the viewer leading, outbid, won or lost', () => {
    const sameAlias = run(connected(), frame(placed(3, { bidder_alias: 'Bidder-you' }))).state;
    expect(sameAlias.leaderAlias).toBe('Bidder-you'); // the UI may say "You" on that row
    expect(sameAlias.own.leading).toBe(false);
    expect(sameAlias.own.sequences).toEqual([]);
    const closedState = run(sameAlias, frame(closed(3))).state;
    expect(closedState.own.result).toBeNull();
  });

  test('own rows are marked by own ack sequence numbers', () => {
    const state = run(connected(), { type: 'bidSent', requestId: 'r1', amount: 1050000, kind: 'bid' }, frame(accepted(3, 'r1')), frame(placed(4)), frame(placed(5, { bidder_alias: 'Bidder-you' }))).state;
    expect(state.own.sequences).toEqual([3]); // 5 matched the alias but had no ack
  });
});

describe('item 17: the viewer own auction', () => {
  test('the seller own auction offers no bid or proxy control', () => {
    const { state } = run(connected(), { type: 'seller', isSeller: true });
    expect(canBid(state)).toBe(false);
  });
});

describe('item 18 and 19: reserve and maximum', () => {
  test('reserve_met null, true and false map to none, met and not met', () => {
    expect(run(connected(), { type: 'snapshot', source: 'fetch', body: snapshot({ reserve_met: null }) }).state.reserveMet).toBeNull();
    expect(run(connected(), frame(placed(3, { reserve_met: true }))).state.reserveMet).toBe(true);
    expect(run(connected(), frame(placed(3, { reserve_met: false }))).state.reserveMet).toBe(false);
  });

  test('your_max lives only in the viewer part, never in the public view', () => {
    const MAX = 77777700; // a value nothing else in the state can hold
    const state = run(connected(), { type: 'snapshot', source: 'fetch', body: snapshot({ viewer: { you_lead: true, your_max: MAX, your_result: null } }) }).state;
    expect(state.own.maximum).toBe(MAX);
    const publicKeys = Object.keys(state).filter((k) => k !== 'own');
    expect(JSON.stringify(publicKeys.map((k) => state[k as keyof RoomState]))).not.toContain(String(MAX));
    const armed = run(state, { type: 'bidSent', requestId: 'p1', amount: MAX + 100, kind: 'proxy' }, frame({ type: 'proxy_armed', request_id: 'p1', auction_id: A, max_amount: MAX + 100, active: true, server_time_ms: T })).state;
    expect(armed.own.maximum).toBe(MAX + 100);
    expect(armed.pending).toBeNull();
  });
});

describe('item 27: degraded', () => {
  test('degraded (system_status or /live) disables bidding and marks the room stale', () => {
    const bySocket = run(connected(), frame({ type: 'system_status', degraded: true, server_time_ms: T })).state;
    expect(canBid(bySocket)).toBe(false);
    expect(isStale(bySocket)).toBe(true);
    const byLive = run(connected(), { type: 'snapshot', source: 'fetch', body: snapshot({ degraded: true }) }).state;
    expect(canBid(byLive)).toBe(false);
  });
});

describe('self-outbid policy (B1 self_outbid_blocked)', () => {
  test('the leader may bid again only when the policy is off', () => {
    const lead = run(connected(), { type: 'bidSent', requestId: 'r1', amount: 1050000, kind: 'bid' }, frame(accepted(3, 'r1'))).state;
    expect(canBid(lead)).toBe(false);
    const open = run(lead, { type: 'snapshot', source: 'fetch', body: snapshot({ sequence_no: 3, self_outbid_blocked: false, viewer: { you_lead: true, your_max: null, your_result: null } }) }).state;
    expect(canBid(open)).toBe(true);
  });
});

describe('slice-2 review fixes: own status and panels', () => {
  const lead3 = (reqId = 'r1') =>
    run(connected(), { type: 'bidSent', requestId: reqId, amount: 1050000, kind: 'bid' }, frame(accepted(3, reqId))).state;

  test('fix 1: arming a maximum refetches, and a you_lead snapshot clears the outbid treatment', () => {
    const outbid = run(lead3(), frame(placed(4)), frame({ type: 'outbid_notice', auction_id: A, current_price: 1100000, sequence_no: 4, server_time_ms: T })).state;
    expect(outbid.own.outbidAt).toBe(1100000);
    const armedStep = roomReducer(
      roomReducer(outbid, { type: 'bidSent', requestId: 'p1', amount: 2000000, kind: 'proxy' }).state,
      frame({ type: 'proxy_armed', request_id: 'p1', auction_id: A, max_amount: 2000000, active: true, server_time_ms: T }),
    );
    expect(armedStep.effects).toContainEqual({ type: 'refetch', reason: 'proxy-armed' });
    expect(armedStep.state.own.outbidAt).toBe(1100000); // proxy_armed alone does not clear it
    const fresh = run(armedStep.state, { type: 'snapshot', source: 'fetch', body: snapshot({ sequence_no: 5, current_price: 1150000, viewer: { you_lead: true, your_max: 2000000, your_result: null } }) }).state;
    expect(fresh.own.leading).toBe(true);
    expect(fresh.own.outbidAt).toBeNull();
  });

  test('fix 2: the accepted result clears when the viewer is outbid', () => {
    const lead = lead3();
    expect(lead.last).toEqual({ kind: 'accepted', sequence: 3, amount: 1050000 });
    const outbid = run(lead, frame({ type: 'outbid_notice', auction_id: A, current_price: 1100000, sequence_no: 4, server_time_ms: T })).state;
    expect(outbid.last).toBeNull();
  });

  test('fix 2: the accepted result clears when a snapshot shows the viewer no longer leading', () => {
    const lost = run(lead3('r2'), { type: 'snapshot', source: 'fetch', body: snapshot({ sequence_no: 4, viewer: { you_lead: false, your_max: null, your_result: null } }) }).state;
    expect(lost.last).toBeNull();
  });
});
