// Which sentence the hero says under the headline (brief 8.1; slice 5 review): the replay of a
// verified run, a run in progress before any run is verified (named by GET /demo, or watched in
// "Try to break it" after starting it here), or the empty state.
import { expect, test } from 'vitest';

import type { DemoOverview } from './contract';
import { heroLede } from './heroLede';

const on = (lastClosed: string | null, activeRun: string | null): { kind: 'on'; overview: DemoOverview } => ({
  kind: 'on',
  overview: {
    enabled: true,
    last_closed_auction_id: lastClosed,
    stress: { enabled: true, active_run_id: activeRun, cooldown_until_ms: null, unavailable_reason: null },
    server_time_ms: 1_800_000_000_000,
  },
});

test('nothing is said until GET /demo has answered', () => {
  expect(heroLede({ kind: 'loading' }, false)).toBe('pending');
  expect(heroLede({ kind: 'loading' }, true)).toBe('pending');
});

test('demo off, or GET /demo failed: the empty state', () => {
  expect(heroLede({ kind: 'off' }, false)).toBe('empty');
  expect(heroLede({ kind: 'failed' }, false)).toBe('empty');
});

test('no verified run yet: a run in progress is said so; otherwise the empty state', () => {
  expect(heroLede(on(null, 'r1'), false)).toBe('running');
  expect(heroLede(on(null, null), true)).toBe('running'); // started here: GET /demo was read before it
  expect(heroLede(on(null, null), false)).toBe('empty');
});

test('a verified run is replayed, whether or not another run is in progress', () => {
  expect(heroLede(on('a1', null), false)).toBe('replay');
  expect(heroLede(on('a1', 'r2'), true)).toBe('replay');
});
