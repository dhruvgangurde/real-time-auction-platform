// The header's unread count (brief 9.1, 9.6; FR-74): read from one page of unread notifications,
// shown up to 99 then "99+", and announced only when it rises after the first read.
import { expect, test } from 'vitest';

import { arrived, badge, unreadCount, UNREAD_LIMIT, UNREAD_POLL_MS } from './unread';

const item = (id: number) => ({ id, type: 'outbid', auction_id: 'a', payload: {}, read: false, created_at_ms: 1 });

test('the count is the unread page; above 99 the badge says 99+', () => {
  expect(UNREAD_LIMIT).toBe(100);
  expect(UNREAD_POLL_MS).toBe(30_000);
  expect(unreadCount({ items: [item(3), item(2)], next_before: null })).toBe(2);
  expect(badge(0)).toBe('');
  expect(badge(7)).toBe('7');
  expect(badge(99)).toBe('99');
  expect(badge(unreadCount({ items: Array.from({ length: 100 }, (_, i) => item(i + 1)), next_before: 1 }))).toBe('99+');
});

test('an arrival is a rise after the first read, never the first read itself', () => {
  expect(arrived(undefined, 4)).toBe(false);
  expect(arrived(4, 4)).toBe(false);
  expect(arrived(4, 2)).toBe(false); // read elsewhere
  expect(arrived(2, 3)).toBe(true);
});
