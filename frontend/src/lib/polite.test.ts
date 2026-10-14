// brief 11.4: at most one polite announcement every 3 s across the whole app. The room and the
// shell (new notifications) write through the same queue, so neither overwrites the other early.
import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import { announcePolite } from './polite';

const region = { textContent: '' as string | null, writes: [] as string[] };

beforeEach(() => {
  vi.useFakeTimers();
  region.writes = [];
  Object.defineProperty(region, 'textContent', {
    configurable: true,
    get: () => region.writes.at(-1) ?? '',
    set: (v: string) => {
      if (v !== '') region.writes.push(v);
    },
  });
  vi.stubGlobal('document', { getElementById: (id: string) => (id === 'live-polite' ? region : null) });
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

test('two callers share one 3 s budget; the latest held text is said once the gap passed', () => {
  announcePolite('Current bid ₹9,500, Bidder-1a2b leads.'); // the room
  announcePolite('New notification. 2 unread.'); // the shell, 0 s later: held
  expect(region.writes).toEqual(['Current bid ₹9,500, Bidder-1a2b leads.']);
  vi.advanceTimersByTime(3_000);
  expect(region.writes).toEqual(['Current bid ₹9,500, Bidder-1a2b leads.', 'New notification. 2 unread.']);
});

test('without a document (tests, server rendering) it does nothing', () => {
  vi.stubGlobal('document', undefined);
  expect(() => announcePolite('x')).not.toThrow();
});
