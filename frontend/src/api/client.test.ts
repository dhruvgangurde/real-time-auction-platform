// brief 13 items 13, 14, 22, 29: the error envelope, request ids, same-id retries, one
// refresh-and-retry, and clock samples from stamped bodies.
import { describe, expect, test } from 'vitest';

import type { Sample } from '../lib/clock';
import { ApiError, createApi, type ApiDeps } from './client';

type Call = { url: string; init: RequestInit };

function fakeFetch(answers: (() => Response | Error)[]): { fetch: typeof globalThis.fetch; calls: Call[] } {
  const calls: Call[] = [];
  const fetchImpl: typeof globalThis.fetch = async (url, init) => {
    calls.push({ url: String(url), init: init ?? {} });
    const next = answers.shift();
    if (next === undefined) throw new Error('unexpected request ' + String(url));
    const answer = next();
    if (answer instanceof Error) throw answer;
    return answer;
  };
  return { fetch: fetchImpl, calls };
}

const json = (status: number, body: unknown, headers: Record<string, string> = {}) => () =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json', ...headers } });

function deps(fetchImpl: typeof globalThis.fetch, extra: Partial<ApiDeps> = {}): ApiDeps & { slept: number[]; samples: Sample[] } {
  const slept: number[] = [];
  const samples: Sample[] = [];
  let t = 1000;
  return {
    fetch: fetchImpl,
    now: () => (t += 10),
    sleep: async (ms) => {
      slept.push(ms);
    },
    getToken: () => 'tok-1',
    refresh: async () => null,
    observe: (s) => samples.push(s),
    slept,
    samples,
    ...extra,
  };
}

const ACK = {
  type: 'bid_accepted', request_id: 'r-1', auction_id: 'a', sequence_no: 7, amount: 1200000,
  end_time_ms: 2, extended: false, extension_count: 0, placed_at_ms: 1, server_time_ms: 5000,
};

describe('the error envelope (item 29)', () => {
  test('a non-2xx body becomes ApiError with code, message and fields', async () => {
    const { fetch } = fakeFetch([
      json(422, { error: { code: 'VALIDATION_ERROR', message: 'check', fields: { amount: 'too low' } } }),
    ]);
    const api = createApi(deps(fetch));
    const error = await api.request('GET', '/x').catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ code: 'VALIDATION_ERROR', message: 'check', status: 422, fields: { amount: 'too low' } });
  });

  test('RATE_LIMITED carries Retry-After; a non-JSON failure becomes HTTP_<status>', async () => {
    const { fetch } = fakeFetch([
      json(429, { error: { code: 'RATE_LIMITED', message: 'slow' } }, { 'Retry-After': '2' }),
      () => new Response('<html>bad gateway</html>', { status: 502, statusText: 'Bad Gateway' }),
    ]);
    const api = createApi(deps(fetch));
    expect(await api.request('GET', '/x').catch((e: unknown) => e)).toMatchObject({ code: 'RATE_LIMITED', retryAfterMs: 2000 });
    expect(await api.request('GET', '/x').catch((e: unknown) => e)).toMatchObject({ code: 'HTTP_502', status: 502 });
  });
});

describe('requests', () => {
  test('sends the bearer from memory, same-origin credentials and JSON', async () => {
    const { fetch, calls } = fakeFetch([json(200, { ok: true, server_time_ms: 5000 })]);
    const api = createApi(deps(fetch));
    await api.request('POST', '/things', { body: { a: 1 } });
    expect(calls[0]?.url).toBe('/api/v1/things');
    expect(calls[0]?.init.credentials).toBe('same-origin');
    expect(calls[0]?.init.headers).toMatchObject({ Authorization: 'Bearer tok-1', 'Content-Type': 'application/json' });
    expect(calls[0]?.init.body).toBe('{"a":1}');
  });

  test('a stamped body feeds the clock with sent and received times (item 12)', async () => {
    const { fetch } = fakeFetch([json(200, { server_time_ms: 5000 })]);
    const d = deps(fetch);
    await createApi(d).request('GET', '/x');
    expect(d.samples).toEqual([{ serverTimeMs: 5000, sentAtMs: 1010, receivedAtMs: 1020 }]);
  });

  test('TOKEN_EXPIRED gets one refresh and one retry, then gives up', async () => {
    const expired = json(401, { error: { code: 'TOKEN_EXPIRED', message: 'expired' } });
    const { fetch, calls } = fakeFetch([expired, json(200, { fine: true }), expired, expired]);
    let refreshes = 0;
    const api = createApi(deps(fetch, { refresh: async () => `tok-${++refreshes + 1}` }));
    expect(await api.request('GET', '/x')).toEqual({ fine: true });
    expect(calls.map((c) => (c.init.headers as Record<string, string>).Authorization)).toEqual(['Bearer tok-1', 'Bearer tok-2']);
    await expect(api.request('GET', '/x')).rejects.toMatchObject({ code: 'TOKEN_EXPIRED' });
    expect(refreshes).toBe(2);
  });

  test('a file body goes as its own bytes with its type, not as JSON, and again on the retry (an image upload)', async () => {
    const expired = json(401, { error: { code: 'TOKEN_EXPIRED', message: 'expired' } });
    const { fetch, calls } = fakeFetch([expired, json(201, { id: 1 })]);
    const file = new Blob([new Uint8Array([0xff, 0xd8, 0xff])], { type: 'image/jpeg' });
    const api = createApi(deps(fetch, { refresh: async () => 'tok-2' }));
    expect(await api.request('POST', '/auctions/a/images', { body: file })).toEqual({ id: 1 });
    for (const call of calls) {
      expect(call.init.body).toBe(file);
      expect((call.init.headers as Record<string, string>)['Content-Type']).toBe('image/jpeg');
    }
  });

  test('a required-auth call with no token refreshes first and fails as INVALID_TOKEN', async () => {
    const { fetch, calls } = fakeFetch([]);
    const api = createApi(deps(fetch, { getToken: () => null }));
    await expect(api.me()).rejects.toMatchObject({ code: 'INVALID_TOKEN', status: 401 });
    expect(calls).toHaveLength(0);
  });

  test('204 is null, a malformed /live body throws', async () => {
    const { fetch } = fakeFetch([() => new Response(null, { status: 204 }), json(200, { status: 'live' })]);
    const api = createApi(deps(fetch));
    expect(await api.request('POST', '/auth/refresh')).toBeNull();
    await expect(api.live('a')).rejects.toMatchObject({ code: 'INTERNAL_ERROR' });
  });
});

describe('bids (items 13 and 14)', () => {
  test('request_id is 1 to 64 of [A-Za-z0-9_-], checked before any request', async () => {
    const { fetch, calls } = fakeFetch([]);
    const api = createApi(deps(fetch));
    await expect(api.placeBid('a', 1000, 'has space')).rejects.toMatchObject({ code: 'VALIDATION_ERROR' });
    await expect(api.setProxy('a', 1000, 'x'.repeat(65))).rejects.toMatchObject({ code: 'VALIDATION_ERROR' });
    expect(calls).toHaveLength(0);
  });

  test('SERVICE_UNAVAILABLE and network failures retry with the same request_id, then succeed', async () => {
    const unavailable = json(503, { error: { code: 'SERVICE_UNAVAILABLE', message: 'redis' } });
    const { fetch, calls } = fakeFetch([unavailable, () => new TypeError('Failed to fetch'), unavailable, json(200, ACK)]);
    const d = deps(fetch);
    const ack = await createApi(d).placeBid('a', 1200000, 'r-1');
    expect(ack.sequence_no).toBe(7);
    expect(calls.map((c) => c.init.body)).toEqual(Array(4).fill('{"amount":1200000,"request_id":"r-1"}'));
    expect(d.slept).toEqual([250, 500, 1000]);
  });

  test('the retry is bounded: six attempts, then the error surfaces', async () => {
    const unavailable = json(503, { error: { code: 'SERVICE_UNAVAILABLE', message: 'redis' } });
    const { fetch, calls } = fakeFetch(Array(7).fill(unavailable));
    const d = deps(fetch);
    await expect(createApi(d).placeBid('a', 1200000, 'r-2')).rejects.toMatchObject({ code: 'SERVICE_UNAVAILABLE' });
    expect(calls).toHaveLength(6);
    expect(d.slept).toEqual([250, 500, 1000, 2000, 4000]);
    expect(new Set(calls.map((c) => c.init.body)).size).toBe(1);
  });

  test('a rejection is not retried and keeps its min_next', async () => {
    const { fetch, calls } = fakeFetch([json(409, { error: { code: 'BID_TOO_LOW', message: 'too low (minimum next bid 1250000)' } })]);
    await expect(createApi(deps(fetch)).placeBid('a', 1200000, 'r-3')).rejects.toMatchObject({ code: 'BID_TOO_LOW', status: 409 });
    expect(calls).toHaveLength(1);
  });
});

// slice 3: discovery, watchlist and notification calls (FR-22 to FR-25, FR-74)
describe('discovery, watchlist and notifications', () => {
  const card = {
    id: 'a1', title: 'K1000', status: 'live', category_id: 3, starting_price: 900000, current_price: null,
    bid_count: 0, start_time_ms: 1, end_time_ms: 2, thumbnail_url: null,
  };

  test('the listing sends the query as given and feeds the clock', async () => {
    const { fetch, calls } = fakeFetch([json(200, { items: [card], page: 2, page_size: 20, has_more: false, server_time_ms: 5000 })]);
    const d = deps(fetch);
    const page = await createApi(d).auctions(new URLSearchParams([['q', 'pentax'], ['status', 'live'], ['status', 'closing'], ['page', '2']]));
    expect(calls[0]?.url).toBe('/api/v1/auctions?q=pentax&status=live&status=closing&page=2');
    expect(page.items).toHaveLength(1);
    expect(d.samples).toHaveLength(1);
  });

  test('categories are read without a token', async () => {
    const { fetch, calls } = fakeFetch([json(200, [])]);
    expect(await createApi(deps(fetch)).categories()).toEqual([]);
    expect(calls[0]?.url).toBe('/api/v1/categories');
    expect(calls[0]?.init.headers).not.toHaveProperty('Authorization');
  });

  test('watch is PUT, unwatch is DELETE, both with the bearer; the list is read with it', async () => {
    const { fetch, calls } = fakeFetch([
      () => new Response(null, { status: 204 }),
      () => new Response(null, { status: 204 }),
      json(200, { items: [card], server_time_ms: 5000 }),
    ]);
    const api = createApi(deps(fetch));
    await api.watch('a1');
    await api.unwatch('a1');
    expect((await api.watchlist()).items[0]?.id).toBe('a1');
    expect(calls.map((c) => `${String(c.init.method)} ${c.url}`)).toEqual([
      'PUT /api/v1/watchlist/a1',
      'DELETE /api/v1/watchlist/a1',
      'GET /api/v1/watchlist',
    ]);
    for (const c of calls) expect(c.init.headers).toMatchObject({ Authorization: 'Bearer tok-1' });
  });

  test('notifications: the unread page, mark one, mark all, preferences', async () => {
    const { fetch, calls } = fakeFetch([
      json(200, { items: [], next_before: null }),
      () => new Response(null, { status: 204 }),
      json(200, { updated: 3 }),
      json(200, { email: { outbid: false } }),
    ]);
    const api = createApi(deps(fetch));
    await api.notifications({ unread: true, before: 40, limit: 100 });
    await api.markRead(41);
    expect(await api.markAllRead()).toBe(3);
    expect(await api.setEmailPreferences({ outbid: false })).toEqual({ outbid: false });
    expect(calls.map((c) => `${String(c.init.method)} ${c.url}`)).toEqual([
      'GET /api/v1/notifications?unread=true&limit=100&before=40',
      'POST /api/v1/notifications/41/read',
      'POST /api/v1/notifications/read-all',
      'PUT /api/v1/notifications/preferences',
    ]);
    expect(calls[3]?.init.body).toBe(JSON.stringify({ email: { outbid: false } }));
  });
});

// FR-65, FR-66, DC-10: the client never sends an amount; a start has no body at all, a
// confirmation exactly the three ids the checkout returned
describe('payments', () => {
  const order = { payment_id: 'p1', settlement_id: 's1', status: 'created', amount: 950000, currency: 'INR', gateway: 'razorpay', key_id: 'rzp_test_X', order_id: 'order_Abc123' };
  const ids = { razorpay_order_id: 'order_Abc123', razorpay_payment_id: 'pay_Xyz789', razorpay_signature: 'a'.repeat(64) };

  test('start: POST with the bearer and no body; confirm: exactly the three ids; the settlement is read', async () => {
    const settlement = { id: 's1', auction_id: 'a1', status: 'pending_payment', final_price: 950000, winner: { id: 'u1', full_name: 'B' }, created_at_ms: 1, paid_at_ms: null, payment: null };
    const { fetch, calls } = fakeFetch([json(200, order), json(202, { status: 'pending' }), json(200, settlement)]);
    const api = createApi(deps(fetch));
    expect((await api.startPayment('a1')).order_id).toBe('order_Abc123');
    expect(await api.confirmPayment('a1', { ...ids, amount: 1 } as typeof ids)).toEqual({ status: 'pending' });
    expect((await api.settlement('a1')).status).toBe('pending_payment');
    expect(calls.map((c) => `${String(c.init.method)} ${c.url}`)).toEqual([
      'POST /api/v1/auctions/a1/payment',
      'POST /api/v1/auctions/a1/payment/confirm',
      'GET /api/v1/auctions/a1/settlement',
    ]);
    expect(calls[0]?.init.body).toBeUndefined();
    expect(calls[0]?.init.headers).toMatchObject({ Authorization: 'Bearer tok-1' });
    expect(JSON.parse(String(calls[1]?.init.body))).toEqual(ids); // the stray amount is not sent
  });
});
