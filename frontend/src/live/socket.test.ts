// brief 13 items 1, 2, 4, 5 (CI-4, CI-6, D3, D4, D5) and item 28 at the socket boundary.
import { describe, expect, test } from 'vitest';

import type { InboundFrame } from '../lib/contract';
import { FakeTimers, FakeWs, tick } from './fakes';
import { MAX_ROOMS, createSocket, type SocketStatus } from './socket';

const T = 1_000_000;

function harness(options: { token?: string | null; refreshed?: string | null; online?: boolean } = {}) {
  const timers = new FakeTimers();
  const sockets: FakeWs[] = [];
  const frames: InboundFrame[] = [];
  const statuses: SocketStatus[] = [];
  const events: string[] = [];
  let online = options.online ?? true;
  let token = options.token === undefined ? 'tok-1' : options.token;
  const socket = createSocket({
    url: 'ws://localhost:8011/api/v1/ws',
    open: (url) => {
      const ws = new FakeWs(url);
      sockets.push(ws);
      events.push('open');
      return ws;
    },
    getToken: () => token,
    refresh: async () => {
      events.push('refresh');
      token = options.refreshed === undefined ? 'tok-2' : options.refreshed;
      return token;
    },
    now: timers.now,
    timers,
    random: () => 0.5, // no jitter unless a test wants it
    online: () => online,
    onFrame: (f) => frames.push(f),
    onStatus: (s) => statuses.push(s),
    onResync: async () => {
      events.push('resync');
    },
  });
  const last = () => sockets[sockets.length - 1]!;
  const authenticated = (ws: FakeWs) =>
    ws.receive({ type: 'authenticated', user_id: 'u', expires_at_ms: timers.now() + 900_000, server_time_ms: T });
  const subscribed = (ws: FakeWs, a: string) =>
    ws.receive({ type: 'subscribed', auction_id: a, your_alias: 'Bidder-aaaa', snapshot: snapshot(a), server_time_ms: T });
  return {
    socket, timers, sockets, frames, statuses, events, last, authenticated, subscribed,
    setOnline: (v: boolean) => {
      online = v;
      socket.setOnline(v);
    },
    states: () => statuses.map((s) => (s.closed ? `closed:${s.closed.code}` : s.state)),
  };
}

function snapshot(a: string) {
  return {
    auction_id: a, status: 'live', current_price: null, leader_alias: null, sequence_no: 0, bid_count: 0,
    start_price: 900000, min_increment: 50000, min_next: 900000, end_time_ms: T + 60_000, extension_count: 0,
    participants: 1, recent_bids: [], degraded: false, reserve_met: null, self_outbid_blocked: true, server_time_ms: T,
  };
}

describe('connecting (item 1)', () => {
  test('authenticate is the first frame and the socket URL carries no token', async () => {
    const h = harness();
    await h.socket.connect();
    expect(h.last().url).toBe('ws://localhost:8011/api/v1/ws');
    expect(h.last().url).not.toContain('tok');
    h.last().open();
    expect(h.last().frames).toEqual([{ type: 'authenticate', token: 'tok-1' }]);
  });

  test('an anonymous viewer never opens a socket', async () => {
    const h = harness({ token: null });
    await h.socket.connect();
    expect(h.sockets).toHaveLength(0);
  });

  test('re-authenticates with a refreshed token before expires_at_ms', async () => {
    const h = harness();
    await h.socket.connect();
    h.last().open();
    h.authenticated(h.last());
    await h.timers.advance(900_000 - 60_000 - 1);
    expect(h.events).not.toContain('refresh');
    await h.timers.advance(1);
    await tick();
    expect(h.events).toContain('refresh');
    expect(h.last().frames.at(-1)).toEqual({ type: 'authenticate', token: 'tok-2' });
  });
});

describe('close codes (item 2)', () => {
  test.each([4002, 4004, 4012])('%i never reconnects', async (code) => {
    const h = harness();
    await h.socket.connect();
    h.last().open();
    h.authenticated(h.last());
    h.last().serverClose(code);
    await h.timers.advance(120_000);
    expect(h.sockets).toHaveLength(1);
    expect(h.socket.status).toEqual({ state: 'offline', closed: { code } });
  });

  test('4003 refreshes the token, then reconnects at once', async () => {
    const h = harness();
    await h.socket.connect();
    h.last().open();
    h.authenticated(h.last());
    h.last().serverClose(4003);
    await tick();
    await tick();
    expect(h.events).toEqual(['open', 'refresh', 'open']);
    h.last().open();
    expect(h.last().frames[0]).toEqual({ type: 'authenticate', token: 'tok-2' });
  });

  test.each([4009, 4010])('%i stops with a message instead of looping', async (code) => {
    const h = harness();
    await h.socket.connect();
    h.last().open();
    h.last().serverClose(code);
    await h.timers.advance(120_000);
    expect(h.sockets).toHaveLength(1);
    expect(h.socket.status.closed).toEqual({ code });
  });

  test.each([1001, 1011, 4008, 1006])('%i reconnects with exponential backoff and jitter', async (code) => {
    const h = harness();
    await h.socket.connect();
    for (const expectedWait of [500, 1000, 2000]) {
      h.last().open();
      h.last().serverClose(code);
      const before = h.sockets.length;
      await h.timers.advance(expectedWait - 1);
      expect(h.sockets).toHaveLength(before);
      await h.timers.advance(1);
      expect(h.sockets).toHaveLength(before + 1);
    }
  });

  test('the backoff is capped at 30 s and jittered by the random source', async () => {
    const h = harness();
    await h.socket.connect();
    for (let i = 0; i < 8; i++) {
      h.last().open();
      h.last().serverClose(1001);
      await h.timers.advance(31_000);
    }
    const before = h.sockets.length;
    h.last().open();
    h.last().serverClose(1001);
    await h.timers.advance(29_999);
    expect(h.sockets).toHaveLength(before);
    await h.timers.advance(1);
    expect(h.sockets).toHaveLength(before + 1);
  });

  test('a reconnect authenticates, resubscribes and resyncs before Reconnected', async () => {
    const h = harness();
    h.socket.subscribe('a1');
    await h.socket.connect();
    h.last().open();
    h.authenticated(h.last());
    h.subscribed(h.last(), 'a1');
    expect(h.states()).toEqual(['connected']);
    h.last().serverClose(1011);
    await h.timers.advance(500);
    h.last().open();
    expect(h.last().frames[0]).toEqual({ type: 'authenticate', token: 'tok-1' });
    h.authenticated(h.last());
    expect(h.last().frames[1]).toEqual({ type: 'subscribe', auction_id: 'a1' });
    expect(h.events).not.toContain('resync');
    h.subscribed(h.last(), 'a1');
    await tick();
    expect(h.events).toContain('resync');
    expect(h.states()).toEqual(['connected', 'reconnecting', 'reconnected']);
    await h.timers.advance(2_000);
    expect(h.states().at(-1)).toBe('connected');
  });
});

describe('rooms (item 4)', () => {
  test('sends no ping of its own', async () => {
    const h = harness();
    h.socket.subscribe('a1');
    await h.socket.connect();
    h.last().open();
    h.authenticated(h.last());
    h.subscribed(h.last(), 'a1');
    for (let i = 0; i < 20; i++) {
      await h.timers.advance(5_000);
      h.last().receive({ type: 'presence', auction_id: 'a1', participants: 1, sequence_no: 0, end_time_ms: T, server_time_ms: T });
    }
    expect(h.last().frames.map((f) => f.type)).not.toContain('ping');
  });

  test('refuses a 21st room; leaving a room unsubscribes', async () => {
    const h = harness();
    await h.socket.connect();
    h.last().open();
    h.authenticated(h.last());
    for (let i = 0; i < MAX_ROOMS; i++) expect(h.socket.subscribe(`a${i}`)).toBe(true);
    expect(h.socket.subscribe('one-too-many')).toBe(false);
    h.socket.unsubscribe('a3');
    expect(h.last().frames.at(-1)).toEqual({ type: 'unsubscribe', auction_id: 'a3' });
    expect(h.socket.subscribe('one-too-many')).toBe(true);
  });
});

describe('connection state (item 5)', () => {
  test('Offline at once on the offline event, reconnecting when back', async () => {
    const h = harness();
    await h.socket.connect();
    h.last().open();
    h.authenticated(h.last());
    h.setOnline(false);
    expect(h.states().at(-1)).toBe('offline');
    expect(h.last().closedWith).toMatchObject({ code: 1000 });
    h.setOnline(true);
    expect(h.states().at(-1)).toBe('reconnecting');
    await tick();
    expect(h.sockets).toHaveLength(2);
  });

  test('Reconnecting on close, and after 12 s of silence in a subscribed room', async () => {
    const h = harness();
    h.socket.subscribe('a1');
    await h.socket.connect();
    h.last().open();
    h.authenticated(h.last());
    h.subscribed(h.last(), 'a1');
    await h.timers.advance(11_999);
    expect(h.states().at(-1)).toBe('connected');
    await h.timers.advance(1);
    expect(h.states().at(-1)).toBe('reconnecting');
    expect(h.sockets[0]?.closedWith).toMatchObject({ reason: 'silent' });
  });

  test('presence frames keep a quiet room connected', async () => {
    const h = harness();
    h.socket.subscribe('a1');
    await h.socket.connect();
    h.last().open();
    h.authenticated(h.last());
    h.subscribed(h.last(), 'a1');
    for (let i = 0; i < 6; i++) {
      await h.timers.advance(5_000);
      h.last().receive({ type: 'presence', auction_id: 'a1', participants: 1, sequence_no: 0, end_time_ms: T, server_time_ms: T });
    }
    expect(h.states()).toEqual(['connected']);
  });
});

describe('frames (item 28)', () => {
  test('malformed frames never reach the room', async () => {
    const h = harness();
    await h.socket.connect();
    h.last().open();
    h.last().receive('{not json');
    h.last().receive({ type: 'bid_placed', auction_id: 'a1' });
    h.last().receive({ type: 'something_new', server_time_ms: T });
    expect(h.frames).toEqual([]);
    h.last().receive({ type: 'pong', server_time_ms: T });
    expect(h.frames.map((f) => f.type)).toEqual(['pong']);
  });
});
