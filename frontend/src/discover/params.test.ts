// Discover's state lives in the URL (brief 9.2, web guidelines "URL reflects state"): the codec
// reads any query string into filters, dropping what it cannot use, writes them back with the
// defaults left out, and builds the API query of GET /auctions (FR-21 to FR-23).
import { describe, expect, test } from 'vitest';

import { EMPTY, MAX_PAGES, apiQuery, fromLocalInput, parseFilters, rangeError, toLocalInput, toSearch, type Filters } from './params';

const q = (s: string) => parseFilters(new URLSearchParams(s));

describe('reading the URL', () => {
  test('an empty query is the default listing', () => {
    expect(q('')).toEqual(EMPTY);
    expect(EMPTY).toEqual({ q: '', category: null, status: [], min: null, max: null, closingBefore: null, pages: 1 });
  });

  test('every filter is read back', () => {
    expect(q('q=pentax+k1000&category=film-cameras&status=live&status=closing&min=1500&max=25000&closing_before=2026-10-20T12:30:00.000Z&page=3')).toEqual({
      q: 'pentax k1000',
      category: 'film-cameras',
      status: ['live', 'closing'],
      min: 1500,
      max: 25000,
      closingBefore: '2026-10-20T12:30:00.000Z',
      pages: 3,
    });
  });

  test('what cannot be used is dropped, so a hand-edited link never reaches a 422', () => {
    expect(
      q('q=%20%20&category=Bad_Slug&status=draft&status=cancelled&status=live&status=live&min=-5&max=1.5&closing_before=tomorrow&page=0&x=1'),
    ).toEqual({ ...EMPTY, status: ['live'] });
    expect(q('min=abc&max=99999999999999999&page=12x').min).toBeNull();
    expect(q(`page=${MAX_PAGES + 5}`).pages).toBe(MAX_PAGES);
  });

  test('a search is trimmed and capped at 200 characters (FR-22)', () => {
    expect(q(`q=${'a'.repeat(250)}`).q).toHaveLength(200);
    expect(q('q=%20camera%20').q).toBe('camera');
  });

  test('statuses keep a fixed order whatever the URL order', () => {
    expect(q('status=closed_no_sale&status=scheduled&status=closed_sold').status).toEqual(['scheduled', 'closed_sold', 'closed_no_sale']);
  });
});

describe('writing the URL', () => {
  test('defaults are left out; the order is stable', () => {
    expect(toSearch(EMPTY).toString()).toBe('');
    const f: Filters = { q: 'k1000', category: 'cameras', status: ['live'], min: 100, max: 200, closingBefore: '2026-10-20T12:30:00.000Z', pages: 2 };
    expect(toSearch(f).toString()).toBe('q=k1000&category=cameras&status=live&min=100&max=200&closing_before=2026-10-20T12%3A30%3A00.000Z&page=2');
  });

  test('a round trip is lossless', () => {
    const f: Filters = { q: 'leica m3', category: 'film', status: ['scheduled', 'closed_sold'], min: 0, max: null, closingBefore: null, pages: 4 };
    expect(parseFilters(toSearch(f))).toEqual(f);
  });
});

describe('the API query', () => {
  test('rupees become integer paise, the category slug its id, one page at a time (DC-6)', () => {
    const f: Filters = { q: 'k1000', category: 'film', status: ['live', 'closing'], min: 1500, max: 25000, closingBefore: '2026-10-20T12:30:00.000Z', pages: 3 };
    expect(apiQuery(f, 7, 2).toString()).toBe(
      'q=k1000&category=7&status=live&status=closing&min_price=150000&max_price=2500000&closing_before=2026-10-20T12%3A30%3A00.000Z&page=2&page_size=20',
    );
  });

  test('the default listing sends only the page (the server defaults to scheduled, live and closing)', () => {
    expect(apiQuery(EMPTY, null, 1).toString()).toBe('page=1&page_size=20');
  });

  test('a minimum above the maximum is caught before any request (FR-23)', () => {
    expect(rangeError({ ...EMPTY, min: 500, max: 100 })).toBe(true);
    expect(rangeError({ ...EMPTY, min: 100, max: 100 })).toBe(false);
    expect(rangeError({ ...EMPTY, min: 500 })).toBe(false);
  });
});

describe('closing before, in the viewer zone (OR-1)', () => {
  test('a datetime-local value becomes a UTC instant and back', () => {
    const iso = fromLocalInput('2026-10-20T18:00');
    expect(iso).toBe(new Date('2026-10-20T18:00').toISOString());
    expect(toLocalInput(iso!)).toBe('2026-10-20T18:00');
    expect(fromLocalInput('')).toBeNull();
    expect(fromLocalInput('not a date')).toBeNull();
  });
});
