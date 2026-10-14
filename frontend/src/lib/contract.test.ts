// brief 13 item 28: unknown types ignored, malformed frames dropped, never a crash; item 18 and
// 19: no room type carries a reserve amount or another bidder's maximum; item 13: request ids.
import { describe, expect, test } from 'vitest';

import {
  REQUEST_ID,
  newRequestId,
  parseAuctionDetail,
  parseAuctionPage,
  parseBidHistoryPage,
  parseCategoryTree,
  parseEmailPreferences,
  parseNotificationPage,
  parseWatchlist,
  parseErrorEnvelope,
  parseFrame,
  parseLiveSnapshot,
  parseTokenResponse,
  parseUserProfile,
} from './contract';

const A = 'b6595bf0-9c27-4003-8a56-e441af8ddb82';
const T = 1791888917462;

const publicSnapshot = {
  auction_id: A,
  status: 'live',
  current_price: 1100000,
  leader_alias: 'Bidder-b19f',
  sequence_no: 5,
  bid_count: 5,
  start_price: 900000,
  min_increment: 50000,
  min_next: 1150000,
  end_time_ms: 1791889807063,
  extension_count: 0,
  participants: 2,
  recent_bids: [
    { sequence_no: 5, amount: 1100000, bidder_alias: 'Bidder-b19f', bid_type: 'manual', placed_at_ms: 1791888917461 },
    { sequence_no: 4, amount: 1050000, bidder_alias: 'Bidder-02aa', bid_type: 'automatic', placed_at_ms: null },
  ],
  degraded: false,
  reserve_met: true,
  self_outbid_blocked: true,
  server_time_ms: T,
};

const samples: Record<string, Record<string, unknown>> = {
  authenticated: { type: 'authenticated', user_id: 'u1', expires_at_ms: T + 900000, server_time_ms: T },
  subscribed: { type: 'subscribed', auction_id: A, your_alias: 'Bidder-b19f', snapshot: publicSnapshot, server_time_ms: T },
  unsubscribed: { type: 'unsubscribed', auction_id: A, server_time_ms: T },
  pong: { type: 'pong', server_time_ms: T },
  bid_accepted: {
    type: 'bid_accepted', request_id: 'ws-1', auction_id: A, sequence_no: 5, amount: 1100000,
    end_time_ms: 1791889807063, extended: false, extension_count: 0, placed_at_ms: 1791888917461, server_time_ms: T,
  },
  bid_rejected: {
    type: 'bid_rejected', request_id: 'ws-2', auction_id: A, code: 'BID_TOO_LOW', message: 'too low',
    current_highest: 1100000, min_next: 1150000, server_time_ms: T,
  },
  proxy_armed: { type: 'proxy_armed', request_id: 'max', auction_id: A, max_amount: 1600000, active: true, server_time_ms: T },
  proxy_rejected: { type: 'proxy_rejected', request_id: 'max', auction_id: A, code: 'BELOW_CURRENT_PRICE', message: 'low', min_next: 1150000, server_time_ms: T },
  error: { type: 'error', code: 'RATE_LIMITED', message: 'slow down', request_id: 'ws-3', server_time_ms: T },
  bid_placed: {
    type: 'bid_placed', auction_id: A, sequence_no: 5, amount: 1100000, bidder_alias: 'Bidder-b19f', bid_type: 'manual',
    end_time_ms: 1791889807063, extended: false, extension_count: 0, placed_at_ms: 1791888917461, reserve_met: true, server_time_ms: T,
  },
  timer_extended: { type: 'timer_extended', auction_id: A, sequence_no: 16, end_time_ms: 1791889927063, extension_count: 1, final: false, server_time_ms: T },
  auction_started: {
    type: 'auction_started', auction_id: A, status: 'live', start_time_ms: T - 1000, end_time_ms: 1791889807063,
    start_price: 900000, min_increment: 50000, min_next: 900000, sequence_no: 0, extension_count: 0, server_time_ms: T,
  },
  auction_closed: { type: 'auction_closed', auction_id: A, outcome: 'closed_sold', final_price: 2200000, reserve_met: true, final_sequence_no: 26, server_time_ms: T },
  presence: { type: 'presence', auction_id: A, participants: 17, sequence_no: 5, end_time_ms: 1791889807063, server_time_ms: T },
  outbid_notice: { type: 'outbid_notice', auction_id: A, current_price: 1250000, sequence_no: 8, server_time_ms: T },
  notification: { type: 'notification', id: 'n1', notification_type: 'auction_won', auction_id: A, payload: { title: 'K1000' }, created_at_ms: T, server_time_ms: T },
  system_status: { type: 'system_status', degraded: true, server_time_ms: T },
};

function without(sample: Record<string, unknown>, key: string): Record<string, unknown> {
  const copy = { ...sample };
  delete copy[key];
  return copy;
}

describe('parseFrame', () => {
  test('every documented inbound type parses from a valid sample, B1 fields included', () => {
    for (const [type, sample] of Object.entries(samples)) {
      const frame = parseFrame(JSON.stringify(sample));
      expect(frame, type).not.toBeNull();
      expect(frame?.type).toBe(type);
    }
    const placed = parseFrame(samples.bid_placed);
    expect(placed).toMatchObject({ placed_at_ms: 1791888917461, reserve_met: true });
    const started = parseFrame(samples.auction_started);
    expect(started).toMatchObject({ status: 'live', sequence_no: 0, min_next: 900000 });
    const subscribed = parseFrame(samples.subscribed);
    expect(subscribed?.type === 'subscribed' && subscribed.snapshot.self_outbid_blocked).toBe(true);
  });

  test('unknown frame types are ignored', () => {
    expect(parseFrame({ type: 'session_replaced', sid: 's', gen: 2, server_time_ms: T })).toBeNull();
    expect(parseFrame({ type: 'nope', server_time_ms: T })).toBeNull();
    expect(parseFrame({ type: 'constructor', server_time_ms: T })).toBeNull();
  });

  test('frames missing or mistyping a required field are dropped', () => {
    expect(parseFrame(without(samples.bid_placed!, 'amount'))).toBeNull();
    expect(parseFrame({ ...samples.bid_placed, amount: '1100000' })).toBeNull();
    expect(parseFrame({ ...samples.bid_placed, amount: -1 })).toBeNull();
    expect(parseFrame({ ...samples.bid_placed, amount: 1.5 })).toBeNull();
    expect(parseFrame({ ...samples.bid_placed, reserve_met: 'yes' })).toBeNull();
    expect(parseFrame({ ...samples.auction_closed, outcome: 'sold' })).toBeNull();
    expect(parseFrame({ ...samples.auction_started, status: 'scheduled' })).toBeNull();
    expect(parseFrame({ ...samples.presence, participants: null })).toBeNull();
    expect(parseFrame({ ...samples.subscribed, snapshot: { ...publicSnapshot, status: 'open' } })).toBeNull();
    expect(parseFrame(without(samples.pong!, 'server_time_ms'))).toBeNull();
  });

  test('placed_at_ms may be absent or null (an old replay), never anything else', () => {
    expect(parseFrame(without(samples.bid_accepted!, 'placed_at_ms'))).toMatchObject({ placed_at_ms: null });
    expect(parseFrame({ ...samples.bid_accepted, placed_at_ms: null })).toMatchObject({ placed_at_ms: null });
    expect(parseFrame({ ...samples.bid_accepted, placed_at_ms: '17' })).toBeNull();
  });

  test('invalid JSON and non-objects are dropped, never thrown', () => {
    for (const raw of ['{', '', 'null', '[]', '"bid_placed"', 42, null, undefined, [1]]) {
      expect(parseFrame(raw)).toBeNull();
    }
  });

  test('a stray field never travels further than the parser', () => {
    const smuggled = parseFrame({ ...samples.bid_placed, reserve_price: 1025000, your_max: 1600000 });
    expect(smuggled).not.toBeNull();
    expect(Object.keys(smuggled!)).not.toContain('reserve_price');
    expect(Object.keys(smuggled!)).not.toContain('your_max');
  });

  test('no room type can carry a reserve amount or another bidder maximum', () => {
    for (const type of ['bid_placed', 'timer_extended', 'auction_started', 'auction_closed', 'presence']) {
      const keys = Object.keys(parseFrame(samples[type])!);
      expect(keys.some((k) => /reserve_price|max_amount|your_max|bidder_id/.test(k)), type).toBe(false);
    }
  });
});

describe('REST guards', () => {
  test('an anonymous /live body has no viewer part; a token-bearing one does', () => {
    const anonymous = parseLiveSnapshot(publicSnapshot);
    expect(anonymous?.viewer).toBeNull();
    expect(Object.keys(anonymous!)).not.toContain('you_lead');
    const own = parseLiveSnapshot({ ...publicSnapshot, you_lead: true, your_max: 1600000, your_result: null });
    expect(own?.viewer).toEqual({ you_lead: true, your_max: 1600000, your_result: null });
    expect(parseLiveSnapshot({ ...publicSnapshot, you_lead: 'yes', your_max: null, your_result: null })).toBeNull();
    expect(parseLiveSnapshot({ ...publicSnapshot, you_lead: true, your_max: null, your_result: 'drew' })).toBeNull();
  });

  test('a snapshot with a malformed recent bid is dropped whole', () => {
    const bad = { ...publicSnapshot, recent_bids: [{ sequence_no: 1 }] };
    expect(parseLiveSnapshot(bad)).toBeNull();
  });

  test('the detail keeps reserve_price only when the server sent it (the owner)', () => {
    const detail = {
      ...publicSnapshot,
      title: 'K1000',
      description: 'A camera',
      category: { id: 3, name: 'Cameras', slug: 'cameras', parent_id: 1 },
      images: [{ id: 1, url: '/media/x.jpg', position: 0 }],
      seller: { id: 's1', name: 'Demo seller' },
      start_time_ms: T - 1000,
      original_end_time_ms: 1791889807063,
      antisnipe_threshold_s: 60,
      antisnipe_extension_s: 120,
      has_reserve: true,
    };
    const everyone = parseAuctionDetail(detail);
    expect(everyone).not.toBeNull();
    expect('reserve_price' in everyone!).toBe(false);
    expect(parseAuctionDetail({ ...detail, reserve_price: 1025000 })?.reserve_price).toBe(1025000);
    expect(parseAuctionDetail({ ...detail, seller: 'x' })).toBeNull();
  });

  test('history page, token, profile and error envelope', () => {
    expect(
      parseBidHistoryPage({ items: [publicSnapshot.recent_bids[0]], next_before: 4, degraded: false })?.items,
    ).toHaveLength(1);
    expect(parseBidHistoryPage({ items: 'none', next_before: null, degraded: false })).toBeNull();
    expect(parseTokenResponse({ access_token: 't', token_type: 'bearer', expires_in: 900 })?.expires_in).toBe(900);
    expect(parseTokenResponse({ access_token: 't' })).toBeNull();
    expect(
      parseUserProfile({ id: 'u', email: 'a@b.c', full_name: 'A', avatar_url: null, status: 'active', roles: ['bidder'] })?.roles,
    ).toEqual(['bidder']);
    expect(parseUserProfile({ id: 'u', email: 'a@b.c', full_name: 'A', avatar_url: null, status: 'active', roles: 'bidder' })).toBeNull();
    expect(parseErrorEnvelope({ error: { code: 'VALIDATION_ERROR', message: 'check', fields: { amount: 'too low', n: 3 } } })).toEqual({
      error: { code: 'VALIDATION_ERROR', message: 'check', fields: { amount: 'too low' } },
    });
    expect(parseErrorEnvelope({ detail: 'nope' })).toBeNull();
  });
});

describe('request ids (item 13)', () => {
  test('a fresh id is 1 to 64 of [A-Za-z0-9_-]', () => {
    expect(newRequestId()).toMatch(REQUEST_ID);
    expect(newRequestId()).not.toBe(newRequestId());
  });

  test('the format is enforced', () => {
    expect(REQUEST_ID.test('abc-_9')).toBe(true);
    expect(REQUEST_ID.test('')).toBe(false);
    expect(REQUEST_ID.test('a'.repeat(65))).toBe(false);
    expect(REQUEST_ID.test('has space')).toBe(false);
  });
});

// slice 3: the shapes of api/discovery.py, catalog.py _card, notifications.py view (FR-22 to
// FR-25, FR-74). A malformed item is dropped, the rest of the page kept; no card carries a reserve.
describe('discovery, watchlist and notification guards', () => {
  const card = {
    id: A, title: 'Pentax K1000', status: 'live', category_id: 3, starting_price: 900000, current_price: 950000,
    bid_count: 2, start_time_ms: T - 60_000, end_time_ms: T + 600_000, thumbnail_url: '/media/a.webp',
  };

  test('a listing page keeps valid cards, drops malformed ones and every stray field', () => {
    const page = parseAuctionPage({
      items: [card, { ...card, id: 7 }, { ...card, id: 'b', current_price: null, thumbnail_url: null, reserve_price: 1 }],
      page: 1, page_size: 20, has_more: true, server_time_ms: T,
    });
    expect(page?.items.map((c) => c.id)).toEqual([A, 'b']);
    expect(page?.items[1]).not.toHaveProperty('reserve_price');
    expect(page).toMatchObject({ page: 1, page_size: 20, has_more: true, server_time_ms: T });
    expect(parseAuctionPage({ items: [card], page: 1, page_size: 20, server_time_ms: T })).toBeNull();
    expect(parseAuctionPage({ ...card, status: 'paused' })).toBeNull();
  });

  test('a watchlist is cards plus the server time; cancelled and closed cards are fine', () => {
    const list = parseWatchlist({ items: [{ ...card, status: 'cancelled' }, { ...card, id: 'c', status: 'closed_sold' }], server_time_ms: T });
    expect(list?.items.map((c) => c.status)).toEqual(['cancelled', 'closed_sold']);
    expect(parseWatchlist({ items: [card] })).toBeNull();
  });

  test('the category tree keeps its nesting; a malformed node is dropped with its children', () => {
    const tree = parseCategoryTree([
      { id: 1, name: 'Cameras', slug: 'cameras', parent_id: null, children: [{ id: 3, name: 'Film', slug: 'film', parent_id: 1, children: [] }] },
      { id: 2, name: 7, slug: 'bad', parent_id: null, children: [{ id: 4, name: 'Lost', slug: 'lost', parent_id: 2, children: [] }] },
    ]);
    expect(tree).toEqual([
      { id: 1, name: 'Cameras', slug: 'cameras', parent_id: null, children: [{ id: 3, name: 'Film', slug: 'film', parent_id: 1, children: [] }] },
    ]);
    expect(parseCategoryTree({ id: 1 })).toBeNull();
  });

  test('a notification page: integer ids, a payload object, read state, keyset cursor', () => {
    const n = { id: 41, type: 'outbid', auction_id: A, payload: { title: 'K1000', current_price: 950000 }, read: false, created_at_ms: T };
    const page = parseNotificationPage({ items: [n, { ...n, id: '42' }, { ...n, id: 43, auction_id: null, read: true }], next_before: 41 });
    expect(page?.items.map((i) => i.id)).toEqual([41, 43]);
    expect(page?.next_before).toBe(41);
    expect(page?.items[0]?.payload).toEqual({ title: 'K1000', current_price: 950000 });
    expect(parseNotificationPage({ items: [n], next_before: 'x' })).toBeNull();
  });

  test('email preferences keep boolean switches only', () => {
    expect(parseEmailPreferences({ email: { outbid: true, ending_soon: false, odd: 'yes' } })).toEqual({ outbid: true, ending_soon: false });
    expect(parseEmailPreferences({ outbid: true })).toBeNull();
  });
});
