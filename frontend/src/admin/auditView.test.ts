// The audit log's before and after as a readable diff (brief 9.9; FR-80, FR-77): only the keys
// that changed (or all, for a creation), money in rupees, times in the viewer's zone, and a
// reserve amount never shown (Q2 of the slice 4 plan, BR-6): "hidden", or "none" without one.
import { expect, test } from 'vitest';

import { auditQuery, diffRows } from './auditView';

const at = (ms: number) => `t${ms}`;

test('an update: the changed keys, money as rupees, times local, the reserve hidden', () => {
  const rows = diffRows(
    { title: 'K1000', starting_price: 900000, reserve_price: 1025000, end_time: 1000, extension_count: 0 },
    { title: 'K1000 SE', starting_price: 950000, reserve_price: 1100000, end_time: 2000, extension_count: 0 },
    at,
  );
  expect(rows).toEqual([
    { key: 'title', before: 'K1000', after: 'K1000 SE' },
    { key: 'starting_price', before: '₹9,000', after: '₹9,500' },
    { key: 'reserve_price', before: 'hidden', after: 'hidden' },
    { key: 'end_time', before: 't1000', after: 't2000' },
  ]);
  expect(JSON.stringify(rows)).not.toMatch(/1025000|1100000|10,250|11,000/);
});

test('a creation or a removal: every key on the side that has it; a reserve removed reads none', () => {
  expect(diffRows(null, { status: 'draft', reserve_price: null }, at)).toEqual([
    { key: 'status', before: '', after: 'draft' },
    { key: 'reserve_price', before: '', after: 'none' },
  ]);
  expect(diffRows({ image_id: 3, object_key: 'auctions/a/b.jpg' }, null, at)).toEqual([
    { key: 'image_id', before: '3', after: '' },
    { key: 'object_key', before: 'auctions/a/b.jpg', after: '' },
  ]);
});

test('the filters in the URL become the API query; anything the API would refuse is dropped', () => {
  const q = (s: string) => auditQuery(new URLSearchParams(s));
  expect(q('').api).toBe('limit=50');
  expect(q('type=auction&id=abc&actor=6f1c2a3e-9d1b-4c55-8a77-0e2f3b4c5d6e').api).toBe(
    'actor_id=6f1c2a3e-9d1b-4c55-8a77-0e2f3b4c5d6e&entity_type=auction&entity_id=abc&limit=50',
  );
  expect(q('actor=not-a-uuid&type=wizard').api).toBe('limit=50');
  expect(q('from=2026-10-17T10:00').api).toBe(`from=${encodeURIComponent(new Date('2026-10-17T10:00').toISOString())}&limit=50`);
});
