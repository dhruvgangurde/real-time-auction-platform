// brief 13 item 12: offset from server_time_ms, the fastest round trip wins, countdowns from
// end_time_ms; item 21: shown in the viewer's zone.
import { expect, test } from 'vitest';

import { ServerClock, formatLocal, toUtcIso } from './clock';

test('keeps the offset from the fastest round trip', () => {
  let local = 1_000_000;
  const clock = new ServerClock(() => local);
  // a slow request: 400 ms round trip, server 5 s ahead
  clock.observe({ sentAtMs: 1_000_000, receivedAtMs: 1_000_400, serverTimeMs: 1_005_200 });
  expect(clock.offset).toBe(5_000);
  // a fast one: 40 ms, server really 5.3 s ahead: wins
  clock.observe({ sentAtMs: 1_001_000, receivedAtMs: 1_001_040, serverTimeMs: 1_006_320 });
  expect(clock.offset).toBe(5_300);
  // a slower one later does not replace it
  clock.observe({ sentAtMs: 1_002_000, receivedAtMs: 1_002_900, serverTimeMs: 1_006_000 });
  expect(clock.offset).toBe(5_300);
  local = 1_003_000;
  expect(clock.now()).toBe(1_008_300);
});

test("a frame's server time raises a too-low offset but never lowers one", () => {
  const clock = new ServerClock(() => 0);
  clock.observe({ sentAtMs: 0, receivedAtMs: 100, serverTimeMs: 1_050 }); // offset 1,000
  clock.observe({ receivedAtMs: 200, serverTimeMs: 1_500 }); // lower bound 1,300: raise
  expect(clock.offset).toBe(1_300);
  clock.observe({ receivedAtMs: 300, serverTimeMs: 1_100 }); // lower bound 800: ignored
  expect(clock.offset).toBe(1_300);
});

test('before any sample the clock is local time', () => {
  const clock = new ServerClock(() => 42);
  expect(clock.offset).toBeNull();
  expect(clock.now()).toBe(42);
});

test('remaining time comes from end_time_ms and the offset, never below zero', () => {
  let local = 10_000;
  const clock = new ServerClock(() => local);
  clock.observe({ sentAtMs: 10_000, receivedAtMs: 10_000, serverTimeMs: 12_000 });
  expect(clock.remainingMs(15_000)).toBe(3_000);
  local = 13_500;
  expect(clock.remainingMs(15_000)).toBe(0);
});

test('formats in the viewer zone, 24-hour', () => {
  const noonUtc = Date.UTC(2026, 9, 14, 12, 0, 7);
  expect(formatLocal(noonUtc, { timeZone: 'Asia/Kolkata', seconds: true })).toBe('17:30:07');
  expect(formatLocal(noonUtc, { timeZone: 'UTC' })).toBe('12:00');
});

test('a datetime-local value becomes UTC ISO and round-trips', () => {
  const iso = toUtcIso('2026-10-14T20:00');
  expect(iso).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/);
  const back = new Date(iso);
  expect([back.getFullYear(), back.getMonth(), back.getDate(), back.getHours(), back.getMinutes()]).toEqual(
    [2026, 9, 14, 20, 0],
  );
  expect(() => toUtcIso('not a date')).toThrow(RangeError);
});
