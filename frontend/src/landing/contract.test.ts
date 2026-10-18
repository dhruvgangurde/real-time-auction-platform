// The demo endpoints' wire shapes (B2: services/demo.py overview, stress_status, start; the checks
// of services/demo_checks.py), guarded like lib/contract.ts: fresh objects with only the declared
// keys, null for anything malformed, never a throw (NFR-33).
import { expect, test } from 'vitest';

import { parseCooldownField, parseDemoOverview, parseStressStart, parseStressStatus } from './contract';

const overview = {
  enabled: true,
  auction_id: null,
  next_start_ms: null,
  last_closed_auction_id: '4f6c2a1e-0000-4000-8000-000000000001',
  stress: { enabled: true, active_run_id: null, cooldown_until_ms: 1_792_300_000_000, unavailable_reason: null },
  server_time_ms: 1_792_299_970_000,
  extra: 'dropped',
};

test('the overview keeps what the landing reads and nothing else', () => {
  expect(parseDemoOverview(overview)).toEqual({
    last_closed_auction_id: '4f6c2a1e-0000-4000-8000-000000000001',
    stress: { enabled: true, active_run_id: null, cooldown_until_ms: 1_792_300_000_000, unavailable_reason: null },
    server_time_ms: 1_792_299_970_000,
  });
  expect(parseDemoOverview({ ...overview, last_closed_auction_id: null })?.last_closed_auction_id).toBeNull();
  expect(parseDemoOverview({ ...overview, stress: { ...overview.stress, unavailable_reason: 'runner_offline' } })?.stress.unavailable_reason).toBe(
    'runner_offline',
  );
});

test('a malformed overview is null', () => {
  expect(parseDemoOverview(null)).toBeNull();
  expect(parseDemoOverview({ ...overview, stress: null })).toBeNull();
  expect(parseDemoOverview({ ...overview, server_time_ms: '1' })).toBeNull();
  expect(parseDemoOverview({ ...overview, stress: { ...overview.stress, cooldown_until_ms: 1.5 } })).toBeNull();
});

test('a start answers the run id, its state and whether it was joined', () => {
  expect(parseStressStart({ run_id: 'r1', state: 'requested', joined: false })).toEqual({ run_id: 'r1', state: 'requested', joined: false });
  expect(parseStressStart({ run_id: 'r1', state: 'running', joined: true })?.joined).toBe(true);
  expect(parseStressStart({ run_id: 'r1', state: 'bogus', joined: true })).toBeNull();
  expect(parseStressStart({ run_id: 1, state: 'running', joined: true })).toBeNull();
});

const check = (id: string, passed: boolean, value: Record<string, unknown>, note?: string) => ({
  id,
  label: `label of ${id}`,
  passed,
  value,
  expected: 'what was expected',
  sql: 'SELECT 1',
  ...(note ? { note } : {}),
});

const status = {
  run_id: 'r1',
  auction_id: 'a1',
  state: 'verified',
  reason: null,
  version: 41,
  bots: { planned: 50, connected: 50 },
  submissions: { sent: 999, accepted: 784, rejected: { BID_TOO_LOW: 210, SELF_OUTBID_NOT_ALLOWED: 5 } },
  watcher: { auction_closed_received: 1, expected: 1 },
  current_price: 4_420_000,
  sequence_no: 784,
  leader_alias: 'Bidder-5c2e',
  end_time_ms: 1_792_300_075_000,
  recent_bids: [{ sequence_no: 784, amount: 4_420_000, bidder_alias: 'Bidder-5c2e', bid_type: 'manual', placed_at_ms: 1_792_300_059_870 }],
  checks: [
    check('gapless', true, { n: 784, lowest: 1, highest: 784, missing: [] }),
    check('distinct', true, { n: 784, distinct: 784 }, 'The schema also guarantees this.'),
  ],
  cooldown_until_ms: null,
  server_time_ms: 1_792_300_090_000,
};

test('a run status parses in every state, with its checks once it has them', () => {
  const parsed = parseStressStatus(status);
  expect(parsed?.checks?.map((c) => [c.id, c.passed, c.note])).toEqual([
    ['gapless', true, null],
    ['distinct', true, 'The schema also guarantees this.'],
  ]);
  expect(parsed?.submissions.rejected).toEqual({ BID_TOO_LOW: 210, SELF_OUTBID_NOT_ALLOWED: 5 });
  expect(parsed?.watcher).toEqual({ received: 1, expected: 1 });
  for (const state of ['requested', 'starting', 'running', 'settling', 'failed', 'aborted']) {
    expect(parseStressStatus({ ...status, state, checks: null })?.state).toBe(state);
  }
  // before the runner has made the lot, and while its live state is rebuilt
  expect(
    parseStressStatus({ ...status, state: 'requested', auction_id: null, current_price: null, end_time_ms: null, recent_bids: [], checks: null }),
  ).not.toBeNull();
  expect(parseStressStatus({ ...status, watcher: { auction_closed_received: null, expected: 1 } })?.watcher.received).toBeNull();
});

test('a malformed status is null, so the page keeps the last good one', () => {
  expect(parseStressStatus({ ...status, state: 'exploded' })).toBeNull();
  expect(parseStressStatus({ ...status, version: '41' })).toBeNull();
  expect(parseStressStatus({ ...status, submissions: { sent: 1, accepted: 1, rejected: { BID_TOO_LOW: 'x' } } })).toBeNull();
  expect(parseStressStatus({ ...status, checks: [{ id: 'gapless' }] })).toBeNull();
  expect(parseStressStatus({ ...status, recent_bids: [{ sequence_no: 1 }] })).toBeNull();
});

test("the cooldown's retry time arrives as a string of milliseconds", () => {
  expect(parseCooldownField({ retry_at_ms: '1792300060000' })).toBe(1_792_300_060_000);
  expect(parseCooldownField({ retry_at_ms: 'soon' })).toBeNull();
  expect(parseCooldownField(undefined)).toBeNull();
});
