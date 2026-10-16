// Watch and unwatch (FR-24, FR-25): optimistic against the cached watchlist. The list changes at
// once, a refusal puts it back, and the server's list is fetched again once it answered. The
// watchlist is ending soonest first, as the server orders it (repositories/watchlist.py).
import type { QueryClient, QueryKey } from '@tanstack/react-query';

import { ApiError, type Api } from '../api/client';
import type { AuctionCard, AuctionDetail, AuctionStatus, Watchlist } from '../lib/contract';

export type WatchVars = { auctionId: string; watch: boolean; card?: AuctionCard };

function toggled(list: Watchlist, v: WatchVars): Watchlist {
  const rest = list.items.filter((c) => c.id !== v.auctionId);
  if (!v.watch || v.card === undefined) return { ...list, items: rest };
  const items = [...rest, v.card].sort((a, b) => a.end_time_ms - b.end_time_ms || a.id.localeCompare(b.id));
  return { ...list, items };
}

export function watchOptions(client: QueryClient, api: Pick<Api, 'watch' | 'unwatch'>, key: QueryKey) {
  return {
    mutationFn: async (v: WatchVars): Promise<void> => {
      try {
        await (v.watch ? api.watch(v.auctionId) : api.unwatch(v.auctionId));
      } catch (error) {
        // already not on the list: that is what was asked for
        if (!v.watch && error instanceof ApiError && error.code === 'NOT_WATCHING') return;
        throw error;
      }
    },
    onMutate: async (v: WatchVars) => {
      await client.cancelQueries({ queryKey: key });
      const previous = client.getQueryData<Watchlist>(key);
      if (previous !== undefined) client.setQueryData<Watchlist>(key, toggled(previous, v));
      return { previous };
    },
    onError: (_error: unknown, _v: WatchVars, context: { previous: Watchlist | undefined } | undefined) => {
      if (context?.previous !== undefined) client.setQueryData(key, context.previous);
    },
    onSettled: () => client.invalidateQueries({ queryKey: key }),
  };
}

/** The room's lot as a card, for the optimistic entry until the watchlist is fetched again; with
 * the room's live status, which moves on after the detail was read (a close seen in the room). */
export function cardFromDetail(d: AuctionDetail, status: AuctionStatus = d.status): AuctionCard {
  const first = [...d.images].sort((a, b) => a.position - b.position)[0];
  return {
    id: d.auction_id,
    title: d.title,
    status,
    category_id: d.category?.id ?? 0,
    starting_price: d.start_price,
    current_price: d.current_price,
    bid_count: d.bid_count,
    start_time_ms: d.start_time_ms,
    end_time_ms: d.end_time_ms,
    thumbnail_url: first?.url ?? null,
  };
}
