// The demo endpoints' wire shapes (B2; brief 8.1, 8.3): GET /demo, POST /demo/stress and
// GET /demo/stress/{run_id} (backend services/demo.py), with the checks of services/demo_checks.py.
// Guarded like lib/contract.ts (brief 13 item 28): every guard builds a fresh object with only the
// declared keys, returns null for anything malformed and never throws. Here, not in lib/contract.ts,
// so the entry never carries them.
import { recentBid, type RecentBid } from '../lib/contract';

export type StressBlock = {
  enabled: boolean;
  active_run_id: string | null;
  cooldown_until_ms: number | null;
  unavailable_reason: string | null;
};

export type DemoOverview = {
  enabled: true;
  /** the lot of the most recent verified stress run: what the hero replays */
  last_closed_auction_id: string | null;
  stress: StressBlock;
  server_time_ms: number;
};

/** GET /demo while demo mode is off: nothing else (B2 Q3, amended in slice 5) */
export type DemoOff = { enabled: false };

export const RUN_STATES = ['requested', 'starting', 'running', 'settling', 'verified', 'failed', 'aborted'] as const;
export type RunState = (typeof RUN_STATES)[number];
export const FINAL_STATES: ReadonlySet<RunState> = new Set(['verified', 'failed', 'aborted']);

export type StressStart = { run_id: string; state: RunState; joined: boolean };

export type CheckResult = {
  id: string;
  label: string;
  passed: boolean;
  value: Record<string, unknown>;
  expected: string;
  sql: string;
  /** the schema also guarantees it (distinct, one_settlement) */
  note: string | null;
};

export type StressStatus = {
  run_id: string;
  auction_id: string | null;
  state: RunState;
  reason: string | null;
  version: number;
  bots: { planned: number; connected: number };
  submissions: { sent: number; accepted: number; rejected: Record<string, number> };
  watcher: { received: number | null; expected: number };
  current_price: number | null;
  sequence_no: number;
  leader_alias: string | null;
  end_time_ms: number | null;
  recent_bids: RecentBid[];
  checks: CheckResult[] | null;
  cooldown_until_ms: number | null;
  server_time_ms: number;
};

type Obj = Record<string, unknown>;
const isObj = (v: unknown): v is Obj => typeof v === 'object' && v !== null && !Array.isArray(v);
const isStr = (v: unknown): v is string => typeof v === 'string';
const isBool = (v: unknown): v is boolean => typeof v === 'boolean';
const isInt = (v: unknown): v is number => typeof v === 'number' && Number.isSafeInteger(v);
const isCount = (v: unknown): v is number => isInt(v) && v >= 0;
const nullOr =
  <T>(check: (v: unknown) => v is T) =>
  (v: unknown): v is T | null =>
    v === null || check(v);
const isState = (v: unknown): v is RunState => isStr(v) && (RUN_STATES as readonly string[]).includes(v);

function stressBlock(v: unknown): StressBlock | null {
  if (!isObj(v)) return null;
  const { enabled, active_run_id, cooldown_until_ms, unavailable_reason } = v;
  if (!isBool(enabled) || !nullOr(isStr)(active_run_id) || !nullOr(isInt)(cooldown_until_ms) || !nullOr(isStr)(unavailable_reason)) {
    return null;
  }
  return { enabled, active_run_id, cooldown_until_ms, unavailable_reason };
}

export function parseDemoOverview(v: unknown): DemoOverview | DemoOff | null {
  if (!isObj(v)) return null;
  if (v.enabled === false) return { enabled: false };
  if (v.enabled !== true || !nullOr(isStr)(v.last_closed_auction_id) || !isInt(v.server_time_ms)) return null;
  const stress = stressBlock(v.stress);
  if (stress === null) return null;
  return { enabled: true, last_closed_auction_id: v.last_closed_auction_id, stress, server_time_ms: v.server_time_ms };
}

export function parseStressStart(v: unknown): StressStart | null {
  if (!isObj(v) || !isStr(v.run_id) || !isState(v.state) || !isBool(v.joined)) return null;
  return { run_id: v.run_id, state: v.state, joined: v.joined };
}

function check(v: unknown): CheckResult | null {
  if (!isObj(v)) return null;
  const { id, label, passed, value, expected, sql, note } = v;
  if (!isStr(id) || !isStr(label) || !isBool(passed) || !isObj(value) || !isStr(expected) || !isStr(sql)) return null;
  if (note !== undefined && !isStr(note)) return null;
  return { id, label, passed, value: { ...value }, expected, sql, note: note ?? null };
}

function counts(v: unknown): Record<string, number> | null {
  if (!isObj(v)) return null;
  const out: Record<string, number> = {};
  for (const [code, n] of Object.entries(v)) {
    if (!isCount(n)) return null;
    out[code] = n;
  }
  return out;
}

export function parseStressStatus(v: unknown): StressStatus | null {
  if (!isObj(v)) return null;
  const { bots, submissions, watcher } = v;
  if (
    !isStr(v.run_id) ||
    !nullOr(isStr)(v.auction_id) ||
    !isState(v.state) ||
    !nullOr(isStr)(v.reason) ||
    !isInt(v.version) ||
    !isObj(bots) ||
    !isCount(bots.planned) ||
    !isCount(bots.connected) ||
    !isObj(submissions) ||
    !isCount(submissions.sent) ||
    !isCount(submissions.accepted) ||
    !isObj(watcher) ||
    !nullOr(isCount)(watcher.auction_closed_received) ||
    !isCount(watcher.expected) ||
    !nullOr(isCount)(v.current_price) ||
    !isInt(v.sequence_no) ||
    !nullOr(isStr)(v.leader_alias) ||
    !nullOr(isInt)(v.end_time_ms) ||
    !Array.isArray(v.recent_bids) ||
    !(v.checks === null || Array.isArray(v.checks)) ||
    !nullOr(isInt)(v.cooldown_until_ms) ||
    !isInt(v.server_time_ms)
  ) {
    return null;
  }
  const rejected = counts(submissions.rejected);
  if (rejected === null) return null;
  const recent: RecentBid[] = [];
  for (const raw of v.recent_bids) {
    const bid = recentBid(raw);
    if (bid === null) return null;
    recent.push(bid);
  }
  let checks: CheckResult[] | null = null;
  if (Array.isArray(v.checks)) {
    checks = [];
    for (const raw of v.checks) {
      const c = check(raw);
      if (c === null) return null;
      checks.push(c);
    }
  }
  return {
    run_id: v.run_id,
    auction_id: v.auction_id,
    state: v.state,
    reason: v.reason,
    version: v.version,
    bots: { planned: bots.planned, connected: bots.connected },
    submissions: { sent: submissions.sent, accepted: submissions.accepted, rejected },
    watcher: { received: watcher.auction_closed_received, expected: watcher.expected },
    current_price: v.current_price,
    sequence_no: v.sequence_no,
    leader_alias: v.leader_alias,
    end_time_ms: v.end_time_ms,
    recent_bids: recent,
    checks,
    cooldown_until_ms: v.cooldown_until_ms,
    server_time_ms: v.server_time_ms,
  };
}

/** DEMO_COOLING_DOWN's fields.retry_at_ms: milliseconds as a string (services/demo.py). */
export function parseCooldownField(fields: Record<string, string> | undefined): number | null {
  const raw = fields?.retry_at_ms;
  return raw !== undefined && /^\d{1,16}$/.test(raw) && Number.isSafeInteger(Number(raw)) ? Number(raw) : null;
}
