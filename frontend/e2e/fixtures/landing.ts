// The landing's helpers (brief 8; slice 5). The e2e stack runs with demo mode off, so the demo
// endpoints are routed in the browser where a spec needs them on, with bodies in the shapes of
// backend services/demo.py and demo_checks.py. The lot the hero replays is real: a closed lot
// with a ledger, a proxy among its bids, made through the API.
import type { APIRequestContext, Page } from '@playwright/test';

import { newAccount, newAuction, restBid, restProxy, waitForStatus } from './stack';

export const RESERVE = 1025000; // ₹10,250
export const MAXIMUM = 1234567; // ₹12,345.67: never a bid amount, so it can only leak
export const SECRETS = ['1025000', '10,250', '1234567', '12,345'];

type Json = Record<string, unknown>;

export function overview(lastClosed: string | null, stress: Json = {}): Json {
  return {
    enabled: true,
    auction_id: null,
    next_start_ms: null,
    last_closed_auction_id: lastClosed,
    stress: { enabled: true, active_run_id: null, cooldown_until_ms: null, unavailable_reason: null, ...stress },
    server_time_ms: Date.now(),
  };
}

const SQL = {
  gapless: 'SELECT count(*) AS n, min(sequence_no) AS lowest, max(sequence_no) AS highest\nFROM bids\nWHERE auction_id = :auction_id',
  distinct: 'SELECT count(*) AS n, count(DISTINCT sequence_no) AS distinct_n\nFROM bids\nWHERE auction_id = :auction_id',
  winner:
    'SELECT s.winner_id, s.final_price,\n       top.bidder_id AS expected_winner_id, top.amount AS expected_price, top.sequence_no\nFROM settlements AS s\nLEFT JOIN LATERAL (\n  SELECT bidder_id, amount, sequence_no\n  FROM bids\n  WHERE auction_id = s.auction_id\n  ORDER BY amount DESC, sequence_no ASC\n  LIMIT 1\n) AS top ON true\nWHERE s.auction_id = :auction_id',
  one_settlement: 'SELECT count(*) AS settlements\nFROM settlements\nWHERE auction_id = :auction_id',
  closed_once:
    "SELECT count(*) AS closes\nFROM audit_log\nWHERE entity_type = 'auction'\n  AND entity_id = :entity_id\n  AND action = 'status_changed'\n  AND before ->> 'status' = 'closing'\n  AND after ->> 'status' IN ('closed_sold', 'closed_no_sale')",
  acked:
    'SELECT cardinality(CAST(:seqs AS bigint[])) AS acknowledged, count(*) AS missing\nFROM unnest(CAST(:seqs AS bigint[]), CAST(:bidders AS uuid[]), CAST(:amounts AS bigint[]))\n     AS ack(sequence_no, bidder_id, amount)\nWHERE NOT EXISTS (\n  SELECT 1 FROM bids AS b\n  WHERE b.auction_id = :auction_id AND b.sequence_no = ack.sequence_no\n    AND b.bidder_id = ack.bidder_id AND b.amount = ack.amount\n)',
};

/** The six Postgres checks of a run with `n` bids, all passing unless `broken` says otherwise. */
export function checks(n: number, broken: 'gapless' | null = null): Json[] {
  const out: Json[] = [
    {
      id: 'gapless',
      label: 'Sequence numbers run from 1 to N with no gaps',
      passed: broken !== 'gapless',
      value: broken === 'gapless' ? { n: n - 1, lowest: 1, highest: n, missing: [57] } : { n, lowest: 1, highest: n, missing: [] },
      expected: 'at least one bid, the lowest number 1 and the highest equal to the count',
      sql: SQL.gapless,
    },
    {
      id: 'distinct',
      label: 'N bids carry N distinct sequence numbers: no duplicates',
      passed: true,
      value: { n, distinct: n },
      expected: 'as many distinct numbers as bids',
      sql: SQL.distinct,
      note: 'The schema also guarantees this: UNIQUE (auction_id, sequence_no) on bids.',
    },
    {
      id: 'winner',
      label: 'One winner: the highest bid, the earlier one on a tie',
      passed: true,
      value: { winner: 'Bidder-7d0a', final_price: 1065000, sequence_no: n, expected_winner: 'Bidder-7d0a', expected_price: 1065000 },
      expected: "one settlement whose winner and price are the ledger's highest bid",
      sql: SQL.winner,
    },
    {
      id: 'one_settlement',
      label: 'One settlement',
      passed: true,
      value: { settlements: 1 },
      expected: 'exactly 1',
      sql: SQL.one_settlement,
      note: 'The schema rules out a second settlement, UNIQUE (auction_id) on settlements; this check proves the one settlement was written.',
    },
    {
      id: 'closed_once',
      label: 'Closed once',
      passed: true,
      value: { closes: 1 },
      expected: 'exactly 1 closing-to-closed transition in the audit log',
      sql: SQL.closed_once,
    },
    {
      id: 'acked',
      label: 'Every bid acknowledged as accepted is in the ledger',
      passed: true,
      value: { acknowledged: n, missing: 0 },
      expected: '0 missing',
      sql: SQL.acked,
    },
  ];
  return out.sort((a, b) => Number(a.passed) - Number(b.passed)); // failing first, as the backend
}

const ALIASES = ['Bidder-7d0a', 'Bidder-31c2', 'Bidder-a94f', 'Bidder-0be7', 'Bidder-5e18', 'Bidder-c6d3'];

/** GET /demo/stress/{run_id} at `state`; `version` grows with each poll the spec serves. */
export function stressStatus(state: string, version: number, over: Json = {}): Json {
  const now = Date.now();
  const sequence = state === 'running' ? 112 : 131;
  const recent = Array.from({ length: 6 }, (_, i) => ({
    sequence_no: sequence - i,
    bidder_alias: ALIASES[i % ALIASES.length],
    amount: 500000 + (sequence - i) * 5000,
    placed_at_ms: now - 2000 - i * 340,
    bid_type: 'manual',
  }));
  const final = ['verified', 'failed', 'aborted'].includes(state);
  return {
    run_id: 'run-e2e',
    auction_id: '00000000-0000-4000-8000-00000000d3e0',
    state,
    reason: state === 'aborted' ? 'the runner restarted' : null,
    version,
    bots: { planned: 50, connected: state === 'requested' || state === 'starting' ? 12 : 50 },
    submissions: { sent: final ? 743 : 604, accepted: final ? 131 : 112, rejected: final ? { BID_TOO_LOW: 597, SELF_OUTBID_NOT_ALLOWED: 15 } : { BID_TOO_LOW: 479, SELF_OUTBID_NOT_ALLOWED: 13 } },
    watcher: { auction_closed_received: state === 'verified' || state === 'failed' ? 1 : null, expected: 1 },
    current_price: 500000 + sequence * 5000,
    sequence_no: sequence,
    leader_alias: 'Bidder-7d0a',
    end_time_ms: final || state === 'settling' ? now - 8000 : now + 39000,
    recent_bids: state === 'running' ? recent : [],
    checks: state === 'verified' ? checks(131) : state === 'failed' ? checks(131, 'gapless') : null,
    cooldown_until_ms: final ? now + 60000 : null,
    server_time_ms: now,
    ...over,
  };
}

export type DemoRoutes = {
  overview: () => Json | null;
  start?: () => { status: number; body: Json; headers?: Record<string, string> };
  status?: () => Json;
};

/** Route the demo endpoints for this page: GET /demo, POST /demo/stress, GET /demo/stress/{id}.
 * A null overview is demo mode off: {"enabled": false}, as the server answers. */
export async function routeDemo(page: Page, routes: DemoRoutes): Promise<void> {
  const notFound = { error: { code: 'NOT_FOUND', message: 'Not found.' } };
  await page.route(
    (url) => url.pathname === '/api/v1/demo' || url.pathname.startsWith('/api/v1/demo/'),
    async (route) => {
      const { pathname } = new URL(route.request().url());
      if (pathname === '/api/v1/demo') {
        const body = routes.overview();
        return route.fulfill({ json: body ?? { enabled: false } });
      }
      if (pathname === '/api/v1/demo/stress' && route.request().method() === 'POST' && routes.start) {
        const answer = routes.start();
        return route.fulfill({ status: answer.status, json: answer.body, headers: answer.headers ?? {} });
      }
      if (pathname.startsWith('/api/v1/demo/stress/') && routes.status) return route.fulfill({ json: routes.status() });
      return route.fulfill({ status: 404, json: notFound });
    },
  );
}

/** Each call answers the next of `items`, then keeps answering the last. */
export function sequence<T>(items: T[]): () => T {
  let i = 0;
  return () => items[Math.min(i++, items.length - 1)]!;
}

export type ReplayLot = { id: string; title: string };

/** A real closed lot for the hero to replay: four bids, two of them by a maximum of ₹12,345.67,
 * sold at ₹10,500 over a ₹10,250 reserve. About 22 s from start to end; ready about 35 s after. */
export async function replayLot(api: APIRequestContext, title: string): Promise<ReplayLot> {
  const seller = await newAccount(api, 'seller', 'seller');
  const alice = await newAccount(api, 'alice');
  const bob = await newAccount(api, 'bob');
  // a 10 s window, so the bids made at the start never extend the end
  const id = await newAuction(api, seller, { title, startInS: 3, endInS: 25, thresholdS: 10, extensionS: 10, reservePrice: RESERVE });
  await waitForStatus(api, id, 'live');
  await (await restBid(api, bob, id, 900000)).json();
  await (await restProxy(api, alice, id, MAXIMUM)).json(); // answers 950000
  await (await restBid(api, bob, id, 1000000)).json(); // answers 1050000: reserve met
  await waitForStatus(api, id, 'closed_sold', 60_000);
  return { id, title };
}
