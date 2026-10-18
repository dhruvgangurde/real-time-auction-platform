// "Try to break it" (brief 8.3; B2 endpoints) as a state machine: what the overview, a start and the
// status polls make of the section. The client keeps the highest status version it has seen.
import { expect, test } from 'vitest';

import type { CheckResult, DemoOverview, StressStatus } from './contract';
import { breakReducer, checkLine, INITIAL, phase, rejectedLine, type BreakState } from './stress';

const NOW = 1_800_000_000_000;

const overview = (stress: Partial<DemoOverview['stress']> = {}): DemoOverview => ({
  last_closed_auction_id: null,
  stress: { enabled: true, active_run_id: null, cooldown_until_ms: null, unavailable_reason: null, ...stress },
  server_time_ms: NOW,
});

const status = (over: Partial<StressStatus> = {}): StressStatus => ({
  run_id: 'r1',
  auction_id: 'a1',
  state: 'running',
  reason: null,
  version: 10,
  bots: { planned: 50, connected: 50 },
  submissions: { sent: 604, accepted: 112, rejected: { BID_TOO_LOW: 479, SELF_OUTBID_NOT_ALLOWED: 13 } },
  watcher: { received: null, expected: 1 },
  current_price: 1_055_000,
  sequence_no: 112,
  leader_alias: 'Bidder-7d0a',
  end_time_ms: NOW + 39_000,
  recent_bids: [],
  checks: null,
  cooldown_until_ms: null,
  server_time_ms: NOW,
  ...over,
});

const apply = (...events: Parameters<typeof breakReducer>[1][]) => events.reduce(breakReducer, INITIAL);

test('the overview decides the first state, in the order a start itself checks', () => {
  expect(apply({ type: 'overview', overview: null, nowMs: NOW }).control).toEqual({ kind: 'unavailable', cause: 'off', retryAtMs: null });
  expect(apply({ type: 'overview', overview: overview(), nowMs: NOW }).control).toEqual({ kind: 'ready' });
  // a run in progress is watched before anything else: one global run (brief 12.4)
  const joined = apply({ type: 'overview', overview: overview({ active_run_id: 'r9', unavailable_reason: 'daily_cap' }), nowMs: NOW });
  expect(joined.control).toEqual({ kind: 'watching' });
  expect([joined.runId, joined.joined]).toEqual(['r9', true]);
  for (const reason of ['disabled', 'runner_offline', 'daily_cap'] as const) {
    expect(apply({ type: 'overview', overview: overview({ unavailable_reason: reason, cooldown_until_ms: NOW + 5_000 }), nowMs: NOW }).control).toEqual({
      kind: 'unavailable',
      cause: reason,
      retryAtMs: null,
    });
  }
  expect(apply({ type: 'overview', overview: overview({ cooldown_until_ms: NOW + 42_000 }), nowMs: NOW }).control).toEqual({ kind: 'cooling', untilMs: NOW + 42_000 });
  expect(apply({ type: 'overview', overview: overview({ cooldown_until_ms: NOW - 1 }), nowMs: NOW }).control).toEqual({ kind: 'ready' });
  expect(apply({ type: 'overviewFailed' }).control).toEqual({ kind: 'unavailable', cause: 'unreachable', retryAtMs: null });
});

test('a start: a new run is watched; an active one is joined', () => {
  const ready = apply({ type: 'overview', overview: overview(), nowMs: NOW });
  const sending = breakReducer(ready, { type: 'send' });
  expect(sending.control).toEqual({ kind: 'sending' });
  const own = breakReducer(sending, { type: 'started', start: { run_id: 'r1', state: 'requested', joined: false } });
  expect([own.control, own.runId, own.joined]).toEqual([{ kind: 'watching' }, 'r1', false]);
  const other = breakReducer(sending, { type: 'started', start: { run_id: 'r2', state: 'running', joined: true } });
  expect([other.runId, other.joined]).toEqual(['r2', true]);
  expect(breakReducer({ ...ready, control: { kind: 'cooling', untilMs: NOW } }, { type: 'send' }).control.kind).toBe('cooling'); // only Ready sends
});

test("a start's refusals become the matching state", () => {
  const sending: BreakState = { ...INITIAL, control: { kind: 'sending' } };
  const failed = (code: string, extra: { reason?: string; retryAtMs?: number | null } = {}) =>
    breakReducer(sending, { type: 'startFailed', code, reason: extra.reason, retryAtMs: extra.retryAtMs ?? null }).control;
  expect(failed('DEMO_COOLING_DOWN', { retryAtMs: NOW + 30_000 })).toEqual({ kind: 'cooling', untilMs: NOW + 30_000 });
  expect(failed('DEMO_COOLING_DOWN')).toEqual({ kind: 'loading' }); // no time given: read the overview again
  expect(failed('DEMO_UNAVAILABLE', { reason: 'runner_offline' })).toEqual({ kind: 'unavailable', cause: 'runner_offline', retryAtMs: null });
  expect(failed('RATE_LIMITED', { retryAtMs: NOW + 1_800_000 })).toEqual({ kind: 'unavailable', cause: 'rate_limited', retryAtMs: NOW + 1_800_000 });
  expect(failed('NOT_FOUND')).toEqual({ kind: 'unavailable', cause: 'off', retryAtMs: null });
  expect(failed('SERVICE_UNAVAILABLE')).toEqual({ kind: 'unavailable', cause: 'unreachable', retryAtMs: null });
});

test('the highest status version wins: an older poll (another instance, a second behind) is dropped', () => {
  const watching: BreakState = { ...INITIAL, control: { kind: 'watching' }, runId: 'r1' };
  const v12 = breakReducer(watching, { type: 'status', status: status({ version: 12, sequence_no: 140 }), nowMs: NOW });
  const v11 = breakReducer(v12, { type: 'status', status: status({ version: 11, sequence_no: 120 }), nowMs: NOW });
  expect(v11.status?.sequence_no).toBe(140);
  const v12again = breakReducer(v12, { type: 'status', status: status({ version: 12, sequence_no: 141 }), nowMs: NOW });
  expect(v12again.status?.sequence_no).toBe(141); // the same version is not older
  expect(breakReducer(watching, { type: 'status', status: status({ run_id: 'other' }), nowMs: NOW }).status).toBeNull();
});

test('a final state stops the watching: the cooldown follows, or the overview is read again', () => {
  const watching: BreakState = { ...INITIAL, control: { kind: 'watching' }, runId: 'r1' };
  const verified = breakReducer(watching, { type: 'status', status: status({ state: 'verified', version: 50, cooldown_until_ms: NOW + 60_000 }), nowMs: NOW });
  expect(verified.control).toEqual({ kind: 'cooling', untilMs: NOW + 60_000 });
  expect(phase(verified)).toBe('verified');
  const aborted = breakReducer(watching, { type: 'status', status: status({ state: 'aborted', version: 50, reason: 'the lot did not open' }), nowMs: NOW });
  expect(aborted.control).toEqual({ kind: 'loading' });
  expect(phase(aborted)).toBe('stopped');
  expect(breakReducer(verified, { type: 'cooled' }).control).toEqual({ kind: 'loading' });
  // the last run's results stay on screen through the cooldown and the next Ready
  const ready = breakReducer(breakReducer(verified, { type: 'cooled' }), { type: 'overview', overview: overview(), nowMs: NOW + 61_000 });
  expect([ready.control, phase(ready)]).toEqual([{ kind: 'ready' }, 'verified']);
});

test('the phase the results panel shows follows the run state, and a failed check fails the run', () => {
  const at = (over: Partial<StressStatus>) => phase({ ...INITIAL, runId: 'r1', status: status(over) });
  expect(phase(INITIAL)).toBe('none');
  expect(phase({ ...INITIAL, control: { kind: 'watching' }, runId: 'r1' })).toBe('running'); // before the first poll
  expect(['requested', 'starting', 'running'].map((s) => at({ state: s as StressStatus['state'] }))).toEqual(['running', 'running', 'running']);
  expect(at({ state: 'settling' })).toBe('settling');
  expect(at({ state: 'verified' })).toBe('verified');
  expect(at({ state: 'failed' })).toBe('failed');
  expect(at({ state: 'aborted' })).toBe('stopped');
});

const check = (id: string, passed: boolean, value: Record<string, unknown>): CheckResult => ({ id, label: id, passed, value, expected: '', sql: '', note: null });

test('each check says what it found, in words, a failure as plainly as a pass', () => {
  expect(checkLine(check('gapless', true, { n: 784, lowest: 1, highest: 784, missing: [] }))).toBe('1 to 784, 784 bids');
  expect(checkLine(check('gapless', false, { n: 783, lowest: 1, highest: 784, missing: [2] }))).toBe('missing: 2');
  expect(checkLine(check('distinct', true, { n: 784, distinct: 784 }))).toBe('784 bids, 784 distinct numbers');
  expect(checkLine(check('winner', true, { winner: 'Bidder-5c2e', final_price: 4_420_000, sequence_no: 784, expected_winner: 'Bidder-5c2e', expected_price: 4_420_000 }))).toBe(
    'Bidder-5c2e at ₹44,200, bid №784',
  );
  expect(checkLine(check('winner', false, { winner: 'Bidder-5c2e', final_price: 4_415_000, sequence_no: 784, expected_winner: 'Bidder-5c2e', expected_price: 4_420_000 }))).toBe(
    'settled: Bidder-5c2e at ₹44,150; the ledger’s highest: Bidder-5c2e at ₹44,200',
  );
  expect(checkLine(check('one_settlement', true, { settlements: 1 }))).toBe('1 settlement');
  expect(checkLine(check('one_settlement', false, { settlements: 0 }))).toBe('0 settlements');
  expect(checkLine(check('closed_once', true, { closes: 1 }))).toBe('closed once');
  expect(checkLine(check('closed_once', false, { closes: 2 }))).toBe('closed 2 times');
  expect(checkLine(check('acked', true, { acknowledged: 784, missing: 0 }))).toBe('784 acknowledged, 0 missing');
  expect(checkLine(check('something_new', true, { x: 1 }))).toBe(''); // a new check shows its label only
  expect(checkLine(check('distinct', true, { n: 'x' }))).toBe(''); // a malformed value shows nothing, never NaN
});

test('rejections by reason, in words', () => {
  expect(rejectedLine({ BID_TOO_LOW: 479, SELF_OUTBID_NOT_ALLOWED: 13 })).toBe('479 too low, 13 already leading');
  expect(rejectedLine({ RATE_LIMITED: 2, TIMEOUT: 1, CLOSED: 1, AUCTION_CLOSED: 3, SOMETHING_ELSE: 4 })).toBe(
    '2 rate limited, 1 no reply in time, 1 socket closed, 3 after the end, 4 something else',
  );
  expect(rejectedLine({})).toBe('');
});
