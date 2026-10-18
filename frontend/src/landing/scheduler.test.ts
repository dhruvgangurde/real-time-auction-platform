// The landing's one anonymous request queue (slice 5 amendment 1): the page's total REST rate stays
// under the 5 per second per-address anonymous limit (brief 12.2) and a 429's Retry-After pauses
// everything. Fake timers: no test waits on the wall clock.
import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import { ApiError } from '../api/client';
import { createScheduler, RATE, WINDOW_MS, type Scheduler } from './scheduler';

let scheduler: Scheduler;
let starts: { name: string; at: number }[];

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(0);
  scheduler = createScheduler({ now: () => Date.now(), setTimer: (fn, ms) => setTimeout(fn, ms) });
  starts = [];
});

afterEach(() => {
  vi.useRealTimers();
});

const job = (name: string, result: () => Promise<string> = () => Promise.resolve(name)) => () => {
  starts.push({ name, at: Date.now() });
  return result();
};

function maxInAnyWindow(times: number[]): number {
  return Math.max(...times.map((t) => times.filter((u) => u >= t && u < t + WINDOW_MS).length));
}

test('never more than the rate in any one-second window, however many are queued', async () => {
  const done = Array.from({ length: 13 }, (_, i) => scheduler.run(job(`page ${i}`)));
  await vi.advanceTimersByTimeAsync(10_000);
  expect(await Promise.all(done)).toHaveLength(13);
  expect(RATE).toBeLessThan(5);
  expect(maxInAnyWindow(starts.map((s) => s.at))).toBe(RATE);
  expect(starts.at(-1)!.at).toBe(3 * WINDOW_MS); // 13 jobs at 4 a second: the last starts at 3 s
});

test('a high-priority request goes before low-priority ones already waiting', async () => {
  for (let i = 0; i < 6; i++) void scheduler.run(job(`ledger ${i}`), 'low');
  void scheduler.run(job('stress status'), 'high');
  await vi.advanceTimersByTimeAsync(5_000);
  const order = starts.map((s) => s.name);
  expect(order.indexOf('stress status')).toBe(RATE); // the first one waiting once the window frees
  expect(order.slice(0, RATE)).toEqual(['ledger 0', 'ledger 1', 'ledger 2', 'ledger 3']);
});

test("a 429 pauses every start until its Retry-After, then retries that request first", async () => {
  let refused = true;
  const limited = job('limited', () => {
    if (refused) {
      refused = false;
      return Promise.reject(new ApiError('RATE_LIMITED', 'slow down', 429, undefined, 2_000));
    }
    return Promise.resolve('limited');
  });
  const first = scheduler.run(limited, 'high');
  await vi.advanceTimersByTimeAsync(0);
  const second = scheduler.run(job('later'));
  await vi.advanceTimersByTimeAsync(1_999);
  expect(starts.map((s) => s.name)).toEqual(['limited']); // paused: nothing else started
  await vi.advanceTimersByTimeAsync(1);
  expect(await first).toBe('limited');
  expect(await second).toBe('later');
  expect(starts.map((s) => [s.name, s.at])).toEqual([
    ['limited', 0],
    ['limited', 2_000],
    ['later', 2_000],
  ]);
});

test('a 429 without Retry-After waits a second; after three refusals the error is the caller’s', async () => {
  const always = job('always', () => Promise.reject(new ApiError('RATE_LIMITED', 'slow down', 429)));
  const result = scheduler.run(always).catch((e: unknown) => e);
  await vi.advanceTimersByTimeAsync(10_000);
  const error = await result;
  expect(error).toBeInstanceOf(ApiError);
  expect((error as ApiError).code).toBe('RATE_LIMITED');
  expect(starts.map((s) => s.at)).toEqual([0, 1_000, 2_000, 3_000]);
});

test('any other error reaches the caller at once, and pauses nothing', async () => {
  const failing = scheduler.run(job('cooling', () => Promise.reject(new ApiError('DEMO_COOLING_DOWN', 'wait', 429)))).catch((e: unknown) => e);
  const next = scheduler.run(job('next'));
  await vi.advanceTimersByTimeAsync(0);
  expect(((await failing) as ApiError).code).toBe('DEMO_COOLING_DOWN');
  expect(await next).toBe('next');
  expect(starts.map((s) => s.at)).toEqual([0, 0]);
});

test('a cancelled request that has not started never starts', async () => {
  let gone = false;
  for (let i = 0; i < RATE; i++) void scheduler.run(job(`busy ${i}`));
  const dropped = scheduler.run(job('unmounted'), 'low', () => gone).catch((e: unknown) => e);
  gone = true;
  await vi.advanceTimersByTimeAsync(5_000);
  expect(starts.map((s) => s.name)).not.toContain('unmounted');
  expect(((await dropped) as ApiError).code).toBe('CANCELLED');
});

test('closed (the landing unmounted), queued requests are dropped and new ones refused; reopened, it runs again', async () => {
  for (let i = 0; i < RATE; i++) void scheduler.run(job(`busy ${i}`));
  const queued = scheduler.run(job('queued')).catch((e: unknown) => e);
  scheduler.close();
  expect(((await queued) as ApiError).code).toBe('CANCELLED');
  expect(((await scheduler.run(job('after close')).catch((e: unknown) => e)) as ApiError).code).toBe('CANCELLED');
  scheduler.open(); // StrictMode mounts twice: the second mount opens it again
  await vi.advanceTimersByTimeAsync(1_000);
  expect(await scheduler.run(job('reopened'))).toBe('reopened');
  expect(starts.map((s) => s.name)).not.toContain('queued');
});
