// brief 13 item 3 (CI-3): one socket per session across tabs, "Use this tab", take-over when
// the owner leaves.
import { expect, test } from 'vitest';

import { FakeLocks, tick } from './fakes';
import { createTabs } from './tabs';

function tab(locks: FakeLocks, log: string[], name: string) {
  return createTabs({
    request: locks.request,
    onOwner: (owner) => log.push(`${name}:${owner ? 'socket' : 'polling'}`),
  });
}

test('only the lock holder opens the socket; the other tab polls', async () => {
  const locks = new FakeLocks();
  const log: string[] = [];
  const a = tab(locks, log, 'A');
  const b = tab(locks, log, 'B');
  void a.claim();
  await tick();
  void b.claim();
  await tick();
  expect(a.owner).toBe(true);
  expect(b.owner).toBe(false);
  expect(log).toEqual(['A:socket']);
});

test('"Use this tab" steals the lock and the old tab falls back to polling', async () => {
  const locks = new FakeLocks();
  const log: string[] = [];
  const a = tab(locks, log, 'A');
  const b = tab(locks, log, 'B');
  void a.claim();
  await tick();
  void b.claim();
  await tick();
  void b.useThisTab();
  await tick();
  await tick();
  expect(b.owner).toBe(true);
  expect(a.owner).toBe(false);
  expect(log[0]).toBe('A:socket');
  expect(log.slice(1).sort()).toEqual(['A:polling', 'B:socket']); // the loser learns asynchronously
});

test('when the owner leaves, the waiting tab takes over', async () => {
  const locks = new FakeLocks();
  const log: string[] = [];
  const a = tab(locks, log, 'A');
  const b = tab(locks, log, 'B');
  void a.claim();
  await tick();
  void b.claim();
  await tick();
  a.releaseLock();
  await tick();
  await tick();
  expect(log).toEqual(['A:socket', 'A:polling', 'B:socket']);
});

test('without the Locks API this tab always owns the socket', async () => {
  const log: string[] = [];
  const solo = createTabs({ request: null, onOwner: (o) => log.push(String(o)) });
  await solo.claim();
  expect(solo.owner).toBe(true);
  expect(log).toEqual(['true']);
});
