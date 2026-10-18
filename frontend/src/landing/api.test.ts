// The landing's calls: anonymous, through the scheduler, with the shapes the backend answers.
import { expect, test } from 'vitest';

import { ApiError } from '../api/client';
import { landingApi } from './api';
import type { Scheduler } from './scheduler';

function harness(answer: (method: string, path: string) => unknown) {
  const calls: { method: string; path: string; auth: unknown; headers: unknown; priority: string }[] = [];
  let priority = '';
  const scheduler: Scheduler = {
    run: (task, p = 'low') => {
      priority = p;
      return task();
    },
    close: () => undefined,
    open: () => undefined,
  };
  const api = {
    request: (method: string, path: string, options: { auth?: unknown; headers?: unknown } = {}) => {
      calls.push({ method, path, auth: options.auth, headers: options.headers, priority });
      const body = answer(method, path);
      return body instanceof Error ? Promise.reject(body) : Promise.resolve(body);
    },
  };
  return { calls, demo: landingApi(api, scheduler) };
}

test('demo off: the overview is null, not an error (every demo route answers 404)', async () => {
  const { demo, calls } = harness(() => new ApiError('NOT_FOUND', 'Not Found', 404));
  expect(await demo.overview()).toBeNull();
  expect(calls).toEqual([{ method: 'GET', path: '/demo', auth: 'none', headers: undefined, priority: 'high' }]);
});

test('any other overview failure reaches the caller', async () => {
  const { demo } = harness(() => new ApiError('SERVICE_UNAVAILABLE', 'down', 503));
  await expect(demo.overview()).rejects.toMatchObject({ code: 'SERVICE_UNAVAILABLE' });
});

test('a start carries X-Requested-With and no token', async () => {
  const { demo, calls } = harness(() => ({ run_id: 'r1', state: 'requested', joined: false }));
  expect(await demo.startStress()).toEqual({ run_id: 'r1', state: 'requested', joined: false });
  expect(calls[0]).toEqual({ method: 'POST', path: '/demo/stress', auth: 'none', headers: { 'X-Requested-With': 'fetch' }, priority: 'high' });
});

test('ledger pages are the largest the API allows, low priority, keyed on before', async () => {
  const { demo, calls } = harness(() => ({ items: [], next_before: null, degraded: false }));
  await demo.ledgerPage('a1');
  await demo.ledgerPage('a1', 301);
  expect(calls.map((c) => [c.path, c.priority])).toEqual([
    ['/auctions/a1/bids?limit=100', 'low'],
    ['/auctions/a1/bids?limit=100&before=301', 'low'],
  ]);
});

test('the live lot and the featured lots are real listings, live first', async () => {
  const page = { items: [], page: 1, page_size: 7, has_more: false, server_time_ms: 1 };
  const { demo, calls } = harness(() => page);
  expect(await demo.newestLive()).toBeNull();
  expect(await demo.featured()).toEqual([]);
  expect(calls.map((c) => c.path)).toEqual(['/auctions?status=live&page_size=1', '/auctions?status=live&status=scheduled&page_size=7']);
  expect(calls.every((c) => c.auth === 'none')).toBe(true);
});
