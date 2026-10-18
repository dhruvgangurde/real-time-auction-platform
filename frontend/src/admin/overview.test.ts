// FR-81's dashboard and NFR-8's counters (GET /admin/overview): the guard keeps only the declared
// fields and returns null for anything malformed; the view says each figure in words.
import { expect, test } from 'vitest';

import { overviewLines, parseOverview } from './overview';

const body = {
  server_time_ms: 1_800_000_000_000,
  bids: { per_second: Array.from({ length: 60 }, (_, i) => (i === 59 ? 3 : 0)), accepted_60s: 12, max_per_second: 3, rejected_60s: { BID_TOO_LOW: 2, RATE_LIMITED: 1 }, rejection_rate: 0.2 },
  sockets: [
    { instance: 'api', sockets: 7, dropped: 0 },
    { instance: 'api-2', sockets: 5, dropped: 2 },
  ],
  queues: { celery_waiting: 1, celery_unacked: 4, outbox_backlog: 0 },
  reconciliation: { gaps_total: 0, last_run: { at_ms: 1_799_999_990_000, checked: 2, gaps: 0, regressions: 0, failed: 0 } },
  errors: { per_minute: [0, 0, 0, 0, 0, 0, 0, 1, 0, 2] },
  health: { postgres: 'ok', redis: 'ok', broker: 'unreachable' },
  recent_audit: [{ id: 9, actor_id: null, entity_type: 'auction', entity_id: 'a1', action: 'status_changed', before: null, after: { status: 'live' }, reason: null, created_at_ms: 1 }],
  extra: 'dropped',
};

test('the overview keeps what the screen shows and nothing else', () => {
  const o = parseOverview(body);
  expect(o).not.toBeNull();
  expect(Object.keys(o!).sort()).toEqual(['bids', 'errors', 'health', 'queues', 'recent_audit', 'reconciliation', 'server_time_ms', 'sockets']);
  expect(o!.sockets).toEqual(body.sockets);
  expect(o!.recent_audit.map((e) => e.id)).toEqual([9]);
  expect(parseOverview({ ...body, reconciliation: { gaps_total: 0, last_run: null } })?.reconciliation.last_run).toBeNull();
});

test('a malformed overview is null', () => {
  expect(parseOverview(null)).toBeNull();
  expect(parseOverview({ ...body, bids: { ...body.bids, accepted_60s: '12' } })).toBeNull();
  expect(parseOverview({ ...body, health: { ...body.health, redis: 'maybe' } })).toBeNull();
  expect(parseOverview({ ...body, sockets: [{ instance: 'api' }] })).toBeNull();
  expect(parseOverview({ ...body, rejected: undefined, bids: { ...body.bids, rejected_60s: { X: -1 } } })).toBeNull();
  expect(parseOverview({ ...body, recent_audit: [{ id: 'x' }] })).toBeNull();
});

test('each figure in words: bids, reasons by count, sockets per instance, queues, gaps, errors', () => {
  const lines = overviewLines(parseOverview(body)!, (ms) => `t${ms}`);
  expect(lines.accepted).toBe('12 accepted, at most 3 in one second.');
  expect(lines.rejected).toBe('3 rejected, 20% of decisions.');
  expect(lines.reasons).toBe('By reason: BID_TOO_LOW 2, RATE_LIMITED 1.');
  expect(lines.sockets).toEqual(['api: 7 connections.', 'api-2: 5 connections, 2 counts dropped.']);
  expect(lines.queues).toEqual(['1 task waiting.', '4 tasks in progress.', '0 bids not yet written to the ledger.']);
  expect(lines.reconciliation).toEqual(['0 gaps found in total.', 'Last run t1799999990000: 2 live auctions checked, 0 gaps, 0 regressions, 0 failed.']);
  expect(lines.errors).toBe('3 in the last 10 minutes.');
  expect(lines.health).toEqual([
    { name: 'Postgres', state: 'Reachable', ok: true },
    { name: 'Redis', state: 'Reachable', ok: true },
    { name: 'Task queue broker', state: 'Unreachable', ok: false },
  ]);
});

test('no rejections and no instances say so', () => {
  const quiet = parseOverview({ ...body, bids: { ...body.bids, rejected_60s: {}, rejection_rate: 0 }, sockets: [], reconciliation: { gaps_total: 0, last_run: null } })!;
  const lines = overviewLines(quiet, String);
  expect(lines.rejected).toBe('0 rejected, 0% of decisions.');
  expect(lines.reasons).toBe('');
  expect(lines.sockets).toEqual(['No API instance has reported in the last 15 seconds.']);
  expect(lines.reconciliation).toEqual(['0 gaps found in total.', 'No reconciliation run is recorded yet.']);
});
