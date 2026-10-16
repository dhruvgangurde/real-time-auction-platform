// The seller's dashboard (FR-19, brief 9.8): every own listing, grouped by where it stands, newest
// first inside each group; the server has no status filter, so the groups are of the loaded pages.
import { expect, test } from 'vitest';

import type { AuctionCard, AuctionStatus } from '../lib/contract';
import { sections } from './dashboard';

const card = (id: string, status: AuctionStatus): AuctionCard => ({
  id, title: id, status, category_id: 1, starting_price: 100, current_price: null, bid_count: 0, start_time_ms: 0, end_time_ms: 1, thumbnail_url: null,
});

test('drafts, scheduled, live and closing, closed, cancelled; empty groups left out; order kept', () => {
  const items = [card('c1', 'closed_sold'), card('d1', 'draft'), card('l1', 'closing'), card('l2', 'live'), card('c2', 'closed_no_sale'), card('d2', 'draft')];
  expect(sections(items).map((s) => [s.key, s.items.map((c) => c.id)])).toEqual([
    ['draft', ['d1', 'd2']],
    ['live', ['l1', 'l2']],
    ['closed', ['c1', 'c2']],
  ]);
  expect(sections([])).toEqual([]);
});
