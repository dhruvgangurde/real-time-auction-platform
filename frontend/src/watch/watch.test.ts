// FR-24: watch and unwatch are optimistic. The cached watchlist changes at once, rolls back on
// a refusal, and is refetched once the server answered. Removing an entry that is already gone
// (NOT_WATCHING) is not an error. Driven through TanStack's own MutationObserver, as the hook is.
import { MutationObserver, QueryClient } from '@tanstack/react-query';
import { describe, expect, test, vi } from 'vitest';

import { ApiError } from '../api/client';
import type { AuctionCard, AuctionDetail, Watchlist } from '../lib/contract';
import { cardFromDetail, watchOptions } from './watch';

const KEY = ['watchlist', 't'];
const card = (id: string, end = 2): AuctionCard => ({
  id, title: id, status: 'live', category_id: 1, starting_price: 100, current_price: null,
  bid_count: 0, start_time_ms: 1, end_time_ms: end, thumbnail_url: null,
});

function setup(answer: () => Promise<void>) {
  const client = new QueryClient();
  client.setQueryData<Watchlist>(KEY, { items: [card('a', 5)], server_time_ms: 1 });
  const calls: string[] = [];
  let release: () => void = () => undefined;
  const gate = new Promise<void>((r) => (release = r));
  const api = {
    watch: async (id: string) => {
      calls.push(`watch ${id}`);
      await gate;
      return answer();
    },
    unwatch: async (id: string) => {
      calls.push(`unwatch ${id}`);
      await gate;
      return answer();
    },
  };
  const observer = new MutationObserver(client, watchOptions(client, api, KEY));
  const ids = () => client.getQueryData<Watchlist>(KEY)?.items.map((c) => c.id);
  return { client, observer, calls, release, ids };
}

describe('watch and unwatch (FR-24)', () => {
  test('adding shows at once, ending-soonest order, and is refetched after the answer', async () => {
    const { client, observer, release, ids } = setup(async () => undefined);
    const done = observer.mutate({ auctionId: 'b', watch: true, card: card('b', 3) });
    await vi.waitFor(() => expect(ids()).toEqual(['b', 'a'])); // before the server answered
    release();
    await done;
    expect(ids()).toEqual(['b', 'a']);
    expect(client.getQueryState(KEY)?.isInvalidated).toBe(true);
  });

  test('a refused add rolls back and keeps the code for the message', async () => {
    const { observer, release, ids } = setup(async () => {
      throw new ApiError('ALREADY_CLOSED', 'cancelled', 409);
    });
    const done = observer.mutate({ auctionId: 'b', watch: true, card: card('b') }).catch((e: unknown) => e);
    await vi.waitFor(() => expect(ids()).toContain('b'));
    release();
    expect(await done).toMatchObject({ code: 'ALREADY_CLOSED' });
    expect(ids()).toEqual(['a']);
  });

  test('removing shows at once; a failure puts it back', async () => {
    const { observer, release, ids } = setup(async () => {
      throw new TypeError('Failed to fetch');
    });
    const done = observer.mutate({ auctionId: 'a', watch: false }).catch(() => undefined);
    await vi.waitFor(() => expect(ids()).toEqual([]));
    release();
    await done;
    expect(ids()).toEqual(['a']);
  });

  test('NOT_WATCHING on remove means it is already gone: no rollback, no error', async () => {
    const { observer, release, ids, calls } = setup(async () => {
      throw new ApiError('NOT_WATCHING', 'gone', 404);
    });
    const done = observer.mutate({ auctionId: 'a', watch: false });
    release();
    await expect(done).resolves.toBeUndefined();
    expect(ids()).toEqual([]);
    expect(calls).toEqual(['unwatch a']);
  });
});

test('the room builds its card from the auction detail', () => {
  const detail = {
    auction_id: 'x', title: 'K1000', status: 'live', category: { id: 4, name: 'Film', slug: 'film', parent_id: null },
    start_price: 900000, current_price: null, bid_count: 0, start_time_ms: 10, end_time_ms: 20,
    images: [{ id: 2, url: '/media/2.webp', position: 1 }, { id: 1, url: '/media/1.webp', position: 0 }],
  } as unknown as AuctionDetail;
  expect(cardFromDetail(detail)).toEqual({
    id: 'x', title: 'K1000', status: 'live', category_id: 4, starting_price: 900000, current_price: null,
    bid_count: 0, start_time_ms: 10, end_time_ms: 20, thumbnail_url: '/media/1.webp',
  });
});
