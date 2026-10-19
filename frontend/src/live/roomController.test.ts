// The one path into the room (brief 11.2): snapshots, polls, socket frames and REST bid
// responses all go through dispatch; polling mode (item 3) and the REST fallback (item 5);
// malformed frames never reach the reducer (item 28).
import { describe, expect, test } from 'vitest';

import { ApiError } from '../api/client';
import type { BidAccepted, InboundFrame, LiveSnapshot } from '../lib/contract';
import { FakeTimers, FakeWs, tick } from './fakes';
import { createRoomController } from './roomController';
import { createSocket } from './socket';

const A = 'a1';
const T = 1_000_000;

function snapshot(seq: number, over: Partial<LiveSnapshot> = {}): LiveSnapshot {
  return {
    auction_id: A, status: 'live', current_price: 900000 + seq * 50000, leader_alias: 'Bidder-0001', sequence_no: seq, bid_count: seq,
    start_price: 900000, min_increment: 50000, min_next: 950000 + seq * 50000, end_time_ms: T + 600_000, extension_count: 0,
    participants: 2, recent_bids: [], degraded: false, reserve_met: null, self_outbid_blocked: true, server_time_ms: T, viewer: null, ...over,
  };
}

function fakeApi(live: () => LiveSnapshot) {
  const calls: string[] = [];
  const bids: { amount: number; id: string }[] = [];
  let bidAnswer: (() => BidAccepted | ApiError) | null = null;
  return {
    calls,
    bids,
    answerBids(fn: () => BidAccepted | ApiError) {
      bidAnswer = fn;
    },
    api: {
      live: async () => {
        calls.push('live');
        return live();
      },
      placeBid: async (_a: string, amount: number, id: string) => {
        bids.push({ amount, id });
        const answer = bidAnswer?.();
        if (answer instanceof ApiError) throw answer;
        if (answer === undefined) throw new Error('no bid answer');
        return answer;
      },
      setProxy: async () => {
        throw new Error('not used');
      },
    },
  };
}

describe('polling mode (item 3)', () => {
  test('a tab without the socket polls /live every 2 s and bids over REST', async () => {
    const timers = new FakeTimers();
    let seq = 1;
    const fake = fakeApi(() => snapshot(seq));
    let hidden = false;
    const room = createRoomController({ auctionId: A, api: fake.api, socket: null, timers, hidden: () => hidden, requestId: () => 'rid-1' });
    await room.start();
    expect(fake.calls).toEqual(['live']);
    expect(room.getState().connection).toBe('polling');
    seq = 2;
    await timers.advance(2_000);
    expect(fake.calls).toEqual(['live', 'live']);
    expect(room.getState().sequence).toBe(2);
    hidden = true; // paused while hidden
    await timers.advance(4_000);
    expect(fake.calls).toHaveLength(2);
    hidden = false;
    await timers.advance(2_000);
    expect(fake.calls).toHaveLength(3);

    fake.answerBids(() => ({ type: 'bid_accepted', request_id: 'rid-1', auction_id: A, sequence_no: 3, amount: 1050000, end_time_ms: T + 600_000, extended: false, extension_count: 0, placed_at_ms: T, server_time_ms: T }));
    expect(room.placeBid(1050000)).toBe('rid-1');
    await tick();
    expect(fake.bids).toEqual([{ amount: 1050000, id: 'rid-1' }]);
    expect(room.getState().own.sequences).toEqual([3]);
    expect(room.getState().pending).toBeNull();
    expect(room.actions.map((a) => a.type)).toEqual(['snapshot', 'snapshot', 'snapshot', 'bidSent', 'frame']);
  });

  test('polling stops once a snapshot says the lot is closed', async () => {
    const timers = new FakeTimers();
    let status: LiveSnapshot['status'] = 'live';
    const fake = fakeApi(() => snapshot(1, { status }));
    const room = createRoomController({ auctionId: A, api: fake.api, socket: null, timers, hidden: () => false, requestId: () => 'rid-1' });
    await room.start();
    status = 'closed_sold';
    await timers.advance(2_000);
    expect(fake.calls).toEqual(['live', 'live']);
    expect(room.getState().terminal).toBe('closed_sold');
    await timers.advance(10_000);
    expect(fake.calls).toHaveLength(2); // a closed lot changes no more: nothing is asked again
  });

  test('a REST rejection reaches the reducer as a bid_rejected frame with min_next', async () => {
    const timers = new FakeTimers();
    const fake = fakeApi(() => snapshot(2));
    const room = createRoomController({ auctionId: A, api: fake.api, socket: null, timers, hidden: () => false, requestId: () => 'rid-2' });
    await room.start();
    fake.answerBids(() => new ApiError('BID_TOO_LOW', 'too low (minimum next bid 1100000)', 409));
    room.placeBid(1000000);
    await tick();
    expect(room.getState().last).toEqual({ kind: 'rejected', code: 'BID_TOO_LOW', minNext: 1100000, what: 'bid' });
    expect(room.getState().pending).toBeNull();
  });
});

describe('socket mode (items 5, 6, 28)', () => {
  function wired() {
    const timers = new FakeTimers();
    const fake = fakeApi(() => snapshot(2));
    const sockets: FakeWs[] = [];
    const frames: InboundFrame[] = [];
    let room: ReturnType<typeof createRoomController> | null = null;
    const socket = createSocket({
      url: 'ws://x/api/v1/ws',
      open: (url) => {
        const ws = new FakeWs(url);
        sockets.push(ws);
        return ws;
      },
      getToken: () => 'tok',
      refresh: async () => 'tok',
      now: timers.now,
      timers,
      random: () => 0.5,
      online: () => true,
      onFrame: (f) => {
        frames.push(f);
        room?.handleFrame(f);
      },
      onStatus: (s) => room?.setConnection(s.closed ? 'polling' : s.state),
      onResync: async () => {
        await room?.resync();
      },
    });
    room = createRoomController({ auctionId: A, api: fake.api, socket, timers, hidden: () => false, requestId: () => 'rid-9' });
    return { timers, fake, sockets, frames, socket, room, last: () => sockets[sockets.length - 1]! };
  }

  test('bids go over the socket when it is open and over REST while it is down', async () => {
    const h = wired();
    await h.room.start();
    await h.socket.connect();
    h.last().open();
    h.last().receive({ type: 'authenticated', user_id: 'u', expires_at_ms: T + 900_000, server_time_ms: T });
    h.last().receive({ type: 'subscribed', auction_id: A, your_alias: 'Bidder-you', snapshot: snapshot(2), server_time_ms: T });
    expect(h.room.getState().subscribed).toBe(true);
    h.room.placeBid(1050000);
    await tick();
    expect(h.last().frames.at(-1)).toEqual({ type: 'place_bid', auction_id: A, amount: 1050000, request_id: 'rid-9' });
    expect(h.fake.bids).toEqual([]);
    h.last().receive({ type: 'bid_accepted', request_id: 'rid-9', auction_id: A, sequence_no: 3, amount: 1050000, end_time_ms: T + 600_000, extended: false, extension_count: 0, placed_at_ms: T, server_time_ms: T });
    expect(h.room.getState().own.sequences).toEqual([3]);

    h.last().serverClose(1011); // down: the next bid goes over REST with its own id
    h.fake.answerBids(() => ({ type: 'bid_accepted', request_id: 'rid-9', auction_id: A, sequence_no: 4, amount: 1100000, end_time_ms: T + 600_000, extended: false, extension_count: 0, placed_at_ms: T, server_time_ms: T }));
    h.room.dispatch({ type: 'snapshot', source: 'fetch', body: snapshot(3, { viewer: { you_lead: false, your_max: null, your_result: null } }) });
    h.room.placeBid(1100000);
    await tick();
    expect(h.fake.bids).toEqual([{ amount: 1100000, id: 'rid-9' }]);
  });

  test('a malformed frame never reaches the reducer', async () => {
    const h = wired();
    await h.room.start();
    await h.socket.connect();
    h.last().open();
    const before = h.room.actions.length;
    h.last().receive('{"type":"bid_placed","auction_id":"a1","amount":"x"}');
    h.last().receive('not json');
    h.last().receive({ type: 'wat', server_time_ms: T });
    expect(h.frames).toEqual([]);
    expect(h.room.actions).toHaveLength(before);
  });

  test('a reconnect resubscribes and refetches before events apply', async () => {
    const h = wired();
    await h.room.start();
    await h.socket.connect();
    h.last().open();
    h.last().receive({ type: 'authenticated', user_id: 'u', expires_at_ms: T + 900_000, server_time_ms: T });
    h.last().receive({ type: 'subscribed', auction_id: A, your_alias: 'Bidder-you', snapshot: snapshot(2), server_time_ms: T });
    h.last().serverClose(1001);
    await h.timers.advance(500);
    h.last().open();
    h.last().receive({ type: 'authenticated', user_id: 'u', expires_at_ms: T + 900_000, server_time_ms: T });
    const callsBefore = h.fake.calls.length;
    h.last().receive({ type: 'subscribed', auction_id: A, your_alias: 'Bidder-you', snapshot: snapshot(5), server_time_ms: T });
    await tick();
    await tick();
    expect(h.fake.calls.length).toBe(callsBefore + 1); // the resync refetch
    expect(h.room.getState().sequence).toBe(5);
    expect(h.room.getState().connection).toBe('reconnected');
  });
});
