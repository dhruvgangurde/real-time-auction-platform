// The listing form's rules (FR-11, FR-12, FR-14, FR-17, BR-4, DC-6, OR-1): what the server would
// refuse is said per field before sending, a create sends paise and UTC, an edit sends only what
// changed and, on a live auction, only title and description; the server's `fields` map back.
import { describe, expect, test } from 'vitest';

import type { AuctionDetail } from '../lib/contract';
import { EMPTY, editable, fromDetail, fromServer, toCreate, toPatch, validate, type ListingValues } from './listing';

const NOW = Date.UTC(2026, 9, 17, 10, 0);
const local = (ms: number) => {
  const d = new Date(ms);
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
};
const good: ListingValues = {
  ...EMPTY,
  title: '  Pentax K1000  ',
  description: 'A 1976 body, light seals replaced.',
  categoryId: '3',
  startingPrice: '9,000',
  minIncrement: '500',
  reserve: '10,250.50',
  start: local(NOW + 3_600_000),
  end: local(NOW + 7_200_000),
};

describe('validate', () => {
  test('a good listing has no problems; reserve and the soft-close defaults are optional', () => {
    expect(validate(good, NOW)).toEqual({});
    expect(validate({ ...good, reserve: '' }, NOW)).toEqual({});
    expect(EMPTY.threshold).toBe('60');
    expect(EMPTY.extension).toBe('120');
  });

  test('every required field says so', () => {
    expect(validate(EMPTY, NOW)).toEqual({
      title: 'required',
      description: 'required',
      categoryId: 'required',
      startingPrice: 'required',
      minIncrement: 'required',
      start: 'required',
      end: 'required',
    });
  });

  test('money: rupees with at most two decimals; starting at least ₹1; increment above zero; reserve not below the start', () => {
    expect(validate({ ...good, startingPrice: '9.999' }, NOW)).toEqual({ startingPrice: 'amount' });
    expect(validate({ ...good, startingPrice: '0.99', reserve: '' }, NOW)).toEqual({ startingPrice: 'startingMin' });
    expect(validate({ ...good, minIncrement: '0' }, NOW)).toEqual({ minIncrement: 'incrementMin' });
    expect(validate({ ...good, reserve: '8,999.99' }, NOW)).toEqual({ reserve: 'reserveBelowStart' });
  });

  test('times: the end after the start; the start not in the past, unless an edit leaves it alone', () => {
    expect(validate({ ...good, end: good.start }, NOW)).toEqual({ end: 'endBeforeStart' });
    expect(validate({ ...good, start: local(NOW - 60_000) }, NOW)).toEqual({ start: 'past' });
    expect(validate({ ...good, start: local(NOW - 60_000) }, NOW, { checkStart: false })).toEqual({});
    expect(validate({ ...good, start: 'soon' }, NOW)).toEqual({ start: 'invalid' });
  });

  test('soft close: whole seconds from 10 to 600', () => {
    for (const bad of ['9', '601', '1.5', 'abc']) {
      expect(validate({ ...good, threshold: bad, extension: bad }, NOW)).toEqual({ threshold: 'seconds', extension: 'seconds' });
    }
    expect(validate({ ...good, threshold: '10', extension: '600' }, NOW)).toEqual({});
  });
});

test('a create sends trimmed text, paise, UTC times and no reserve field when there is none', () => {
  expect(toCreate(good)).toEqual({
    title: 'Pentax K1000',
    description: 'A 1976 body, light seals replaced.',
    category_id: 3,
    starting_price: 900000,
    min_increment: 50000,
    reserve_price: 1025050,
    start_time: new Date(NOW + 3_600_000).toISOString(),
    end_time: new Date(NOW + 7_200_000).toISOString(),
    antisnipe_threshold_s: 60,
    antisnipe_extension_s: 120,
  });
  expect(toCreate({ ...good, reserve: '' })).not.toHaveProperty('reserve_price');
});

describe('an edit', () => {
  test('sends only what changed; clearing the reserve sends null', () => {
    expect(toPatch(good, good, 'all')).toEqual({});
    expect(toPatch({ ...good, title: 'Pentax K1000 SE', reserve: '' }, good, 'all')).toEqual({ title: 'Pentax K1000 SE', reserve_price: null });
  });

  test('on a live auction without bids, only title and description go', () => {
    const changed = { ...good, title: 'New', startingPrice: '1', end: local(NOW + 9_000_000) };
    expect(toPatch(changed, good, 'descriptive')).toEqual({ title: 'New' });
  });

  test('what can change, by status (FR-17, BR-4: "a bid exists" is the room sequence, D8)', () => {
    expect(editable('draft', 0)).toBe('all');
    expect(editable('scheduled', 0)).toBe('all');
    expect(editable('live', 0)).toBe('descriptive');
    expect(editable('live', 1)).toBe('none');
    for (const status of ['closing', 'closed_sold', 'closed_no_sale', 'cancelled'] as const) expect(editable(status, 0)).toBe('none');
  });
});

test('a stored listing reads back into the form unchanged, the reserve only when the owner got it (BR-6)', () => {
  const detail = {
    title: 'Pentax K1000',
    description: 'A 1976 body.',
    category: { id: 3, name: 'Cameras', slug: 'cameras', parent_id: null },
    start_price: 900000,
    min_increment: 50000,
    reserve_price: 1025050,
    start_time_ms: NOW + 3_600_000,
    end_time_ms: NOW + 7_200_000,
    antisnipe_threshold_s: 20,
    antisnipe_extension_s: 30,
  } as AuctionDetail;
  const values = fromDetail(detail);
  expect(values).toMatchObject({ categoryId: '3', startingPrice: '9,000', minIncrement: '500', reserve: '10,250.50', threshold: '20', extension: '30' });
  expect(toPatch(values, values, 'all')).toEqual({});
  expect(fromDetail({ ...detail, reserve_price: null }).reserve).toBe('');
});

test("the server's field errors land on the same fields, in the form's own words", () => {
  expect(
    fromServer({
      end_time: 'must be later than start_time',
      start_time: 'must not be in the past',
      reserve_price: 'must not be less than starting_price',
      antisnipe_threshold_s: 'must be between 10 and 600 seconds',
      category_id: 'unknown category',
      images: 'at least one image is required (FR-13)',
      starting_price: 'Input should be greater than or equal to 100',
      body: 'something general',
    }),
  ).toEqual({
    end: 'endBeforeStart',
    start: 'past',
    reserve: 'reserveBelowStart',
    threshold: 'seconds',
    categoryId: 'category',
    images: 'images',
    startingPrice: 'startingMin',
  });
});
