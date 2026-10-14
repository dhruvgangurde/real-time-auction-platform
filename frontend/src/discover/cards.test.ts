// What a card says in each status (brief 9.2), the refetch when a live card's server-derived
// countdown reaches zero, and the list poll that stops once more than three pages are loaded.
import { describe, expect, test } from 'vitest';

import type { AuctionCard } from '../lib/contract';
import { POLL_MS, cardView, cardWatch, crossedZero, pollInterval } from './cards';

const NOW = 1_800_000_000_000;
const card = (over: Partial<AuctionCard> = {}): AuctionCard => ({
  id: 'a', title: 'K1000', status: 'live', category_id: 1, starting_price: 900000, current_price: 950000,
  bid_count: 2, start_time_ms: NOW - 60_000, end_time_ms: NOW + 90_000, thumbnail_url: null, ...over,
});

describe('a card in each status', () => {
  test('scheduled: the starting bid and when it starts', () => {
    expect(cardView(card({ status: 'scheduled', current_price: null, bid_count: 0, start_time_ms: NOW + 3_600_000 }), NOW)).toEqual({
      kind: 'scheduled', price: 900000, startsAtMs: NOW + 3_600_000,
    });
  });

  test('live: the current bid, or the starting bid before the first one, with the time left', () => {
    expect(cardView(card(), NOW)).toEqual({ kind: 'live', price: 950000, hasBids: true, bids: 2, endTimeMs: NOW + 90_000, remainingMs: 90_000 });
    expect(cardView(card({ current_price: null, bid_count: 0 }), NOW)).toMatchObject({ kind: 'live', price: 900000, hasBids: false });
  });

  test('live past its end is closing, with no countdown, until a refetch says otherwise', () => {
    expect(cardView(card({ end_time_ms: NOW }), NOW)).toEqual({ kind: 'closing', price: 950000, hasBids: true, bids: 2 });
    expect(cardView(card({ status: 'closing' }), NOW)).toMatchObject({ kind: 'closing' });
  });

  test('closed: sold for the final price, or not sold; cancelled', () => {
    expect(cardView(card({ status: 'closed_sold' }), NOW)).toEqual({ kind: 'sold', price: 950000 });
    expect(cardView(card({ status: 'closed_no_sale' }), NOW)).toEqual({ kind: 'noSale' });
    expect(cardView(card({ status: 'cancelled' }), NOW)).toEqual({ kind: 'cancelled' });
  });
});

describe('the zero-crossing refetch', () => {
  test('fires when a live card ends between two ticks, not before and not again', () => {
    const cards = [card({ end_time_ms: NOW + 500 }), card({ id: 'b', status: 'scheduled', end_time_ms: NOW + 500 })];
    expect(crossedZero(cards, NOW - 1000, NOW)).toBe(false);
    expect(crossedZero(cards, NOW, NOW + 1000)).toBe(true);
    expect(crossedZero(cards, NOW + 1000, NOW + 2000)).toBe(false);
  });

  test('a card already past its end at load does not fire (no refetch loop)', () => {
    expect(crossedZero([card({ end_time_ms: NOW - 5000 })], NOW, NOW + 1000)).toBe(false);
  });
});

describe('the list poll (amendment)', () => {
  test('every 15 s for up to three pages, then off', () => {
    expect(POLL_MS).toBe(15_000);
    for (const pages of [0, 1, 2, 3]) expect(pollInterval(pages), String(pages)).toBe(15_000);
    for (const pages of [4, 10, 25]) expect(pollInterval(pages), String(pages)).toBe(false);
  });
});

describe('the Watch toggle on a card', () => {
  test('a lot still to be decided offers Watch; a finished one only Watching, so it can be removed', () => {
    for (const status of ['scheduled', 'live', 'closing'] as const) {
      expect(cardWatch(status, false), status).toBe(true);
      expect(cardWatch(status, true), status).toBe(true);
    }
    for (const status of ['closed_sold', 'closed_no_sale', 'cancelled'] as const) {
      expect(cardWatch(status, false), status).toBe(false);
      expect(cardWatch(status, true), status).toBe(true);
    }
  });
});
