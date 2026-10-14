// The watchlist page keeps an item the viewer removed, with "Watch again", until they leave the
// page (the undo for a removal); everything else follows the server's list, ending soonest first.
import { expect, test } from 'vitest';

import type { AuctionCard } from '../lib/contract';
import { kept } from './kept';

const card = (id: string, end: number, price: number | null = null): AuctionCard => ({
  id, title: id, status: 'live', category_id: 1, starting_price: 100, current_price: price,
  bid_count: 0, start_time_ms: 1, end_time_ms: end, thumbnail_url: null,
});

test('the first list is shown as the server sent it', () => {
  expect(kept([], [card('a', 1), card('b', 2)]).map((c) => c.id)).toEqual(['a', 'b']);
});

test('a removed item stays in its place; a fresh one takes the new values', () => {
  const shown = [card('a', 1), card('b', 2), card('c', 3)];
  const next = kept(shown, [card('a', 1), card('c', 3, 500)]);
  expect(next.map((c) => c.id)).toEqual(['a', 'b', 'c']);
  expect(next[2]?.current_price).toBe(500);
});

test('an item added elsewhere joins in ending-soonest order', () => {
  expect(kept([card('a', 1), card('c', 3)], [card('a', 1), card('b', 2), card('c', 3)]).map((c) => c.id)).toEqual(['a', 'b', 'c']);
});
