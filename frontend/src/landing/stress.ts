// "Try to break it" (brief 8.3; B2 Q7, the endpoints of services/demo.py) as a state machine.
// The control (the button and what stands in for it) follows GET /demo, a start's answer and the
// run's status; the results panel follows the run state. The client keeps the highest status
// version it has seen, because either instance may answer a poll a second behind the other.
import { formatINR, isPaise } from '../lib/money';
import { landingEnIN } from '../strings/landing-en-IN';
import { FINAL_STATES, type CheckResult, type DemoOverview, type StressStart, type StressStatus } from './contract';

const t = landingEnIN.break;

export type Cause = 'off' | 'disabled' | 'runner_offline' | 'daily_cap' | 'rate_limited' | 'unreachable';

export type Control =
  | { kind: 'loading' }
  | { kind: 'ready' }
  | { kind: 'sending' }
  | { kind: 'watching' }
  | { kind: 'cooling'; untilMs: number }
  | { kind: 'unavailable'; cause: Cause; retryAtMs: number | null };

export type BreakState = {
  control: Control;
  runId: string | null;
  /** the run was already in progress: "A run is in progress. Watching it." */
  joined: boolean;
  status: StressStatus | null;
};

export type BreakEvent =
  | { type: 'overview'; overview: DemoOverview | null; nowMs: number }
  | { type: 'overviewFailed' }
  | { type: 'send' }
  | { type: 'started'; start: StressStart }
  | { type: 'startFailed'; code: string; reason?: string | undefined; retryAtMs: number | null }
  | { type: 'status'; status: StressStatus; nowMs: number }
  | { type: 'cooled' };

export const INITIAL: BreakState = { control: { kind: 'loading' }, runId: null, joined: false, status: null };

const CAUSES = new Set<string>(['disabled', 'runner_offline', 'daily_cap']);
const unavailable = (cause: Cause, retryAtMs: number | null = null): Control => ({ kind: 'unavailable', cause, retryAtMs });
const asCause = (reason: string | null | undefined): Cause => (reason != null && CAUSES.has(reason) ? (reason as Cause) : 'disabled');

function fromOverview(s: BreakState, o: DemoOverview | null, nowMs: number): BreakState {
  if (o === null) return { ...s, control: unavailable('off') };
  const { active_run_id: active, unavailable_reason: reason, enabled, cooldown_until_ms: until } = o.stress;
  if (active !== null) {
    // one global run (brief 12.4): watch it, whoever started it
    const same = active === s.runId;
    return { control: { kind: 'watching' }, runId: active, joined: same ? s.joined : true, status: same ? s.status : null };
  }
  if (reason !== null) return { ...s, control: unavailable(asCause(reason)) };
  if (!enabled) return { ...s, control: unavailable('disabled') };
  if (until !== null && until > nowMs) return { ...s, control: { kind: 'cooling', untilMs: until } };
  return { ...s, control: { kind: 'ready' } };
}

export function breakReducer(s: BreakState, e: BreakEvent): BreakState {
  switch (e.type) {
    case 'overview':
      return fromOverview(s, e.overview, e.nowMs);
    case 'overviewFailed':
      return { ...s, control: unavailable('unreachable') };
    case 'send':
      return s.control.kind === 'ready' ? { ...s, control: { kind: 'sending' } } : s;
    case 'started': {
      const same = e.start.run_id === s.runId;
      return { control: { kind: 'watching' }, runId: e.start.run_id, joined: e.start.joined, status: same ? s.status : null };
    }
    case 'startFailed':
      if (e.code === 'DEMO_COOLING_DOWN') {
        return { ...s, control: e.retryAtMs === null ? { kind: 'loading' } : { kind: 'cooling', untilMs: e.retryAtMs } };
      }
      if (e.code === 'DEMO_UNAVAILABLE') return { ...s, control: unavailable(asCause(e.reason)) };
      if (e.code === 'RATE_LIMITED') return { ...s, control: unavailable('rate_limited', e.retryAtMs) };
      if (e.code === 'NOT_FOUND') return { ...s, control: unavailable('off') };
      return { ...s, control: unavailable('unreachable') };
    case 'status': {
      if (e.status.run_id !== s.runId) return s;
      if (s.status !== null && e.status.version < s.status.version) return s; // an older poll
      if (!FINAL_STATES.has(e.status.state)) return { ...s, status: e.status };
      const until = e.status.cooldown_until_ms;
      const control: Control = until !== null && until > e.nowMs ? { kind: 'cooling', untilMs: until } : { kind: 'loading' };
      return { ...s, status: e.status, control };
    }
    case 'cooled':
      return { ...s, control: { kind: 'loading' } };
  }
}

export type Phase = 'none' | 'running' | 'settling' | 'verified' | 'failed' | 'stopped';

/** What the results panel shows: the run's own state, never a guess. */
export function phase(s: BreakState): Phase {
  const state = s.status?.state;
  if (state === undefined) return s.control.kind === 'watching' && s.runId !== null ? 'running' : 'none';
  if (state === 'settling') return 'settling';
  if (state === 'verified' || state === 'failed') return state;
  if (state === 'aborted') return 'stopped';
  return 'running';
}

const count = (v: unknown): number | null => (typeof v === 'number' && Number.isSafeInteger(v) && v >= 0 ? v : null);
const text = (v: unknown): string | null => (typeof v === 'string' ? v : null);
const money = (v: unknown): string | null => (isPaise(v) ? formatINR(v) : null);

/** What a check found, in words (services/demo_checks.py values); '' when it cannot be said. */
export function checkLine(c: CheckResult): string {
  const v = c.value;
  switch (c.id) {
    case 'gapless': {
      const n = count(v.n);
      const lowest = count(v.lowest);
      const highest = count(v.highest);
      const missing = Array.isArray(v.missing) ? v.missing.map(count) : [];
      if (!c.passed && missing.length > 0 && missing.every((m) => m !== null)) return t.values.missing(missing.join(', '));
      return n !== null && lowest !== null && highest !== null ? t.values.gapless(lowest, highest, n) : '';
    }
    case 'distinct': {
      const n = count(v.n);
      const distinct = count(v.distinct);
      return n !== null && distinct !== null ? t.values.distinct(n, distinct) : '';
    }
    case 'winner': {
      const winner = text(v.winner);
      const price = money(v.final_price);
      const seq = count(v.sequence_no);
      if (c.passed) return winner !== null && price !== null && seq !== null ? t.values.winner(winner, price, seq) : '';
      const none = t.values.none;
      return t.values.winnerFail(winner ?? none, price ?? none, text(v.expected_winner) ?? none, money(v.expected_price) ?? none);
    }
    case 'one_settlement': {
      const n = count(v.settlements);
      return n === null ? '' : t.values.settlements(n);
    }
    case 'closed_once': {
      const n = count(v.closes);
      return n === null ? '' : t.values.closes(n);
    }
    case 'acked': {
      const acknowledged = count(v.acknowledged);
      const missing = count(v.missing);
      return acknowledged !== null && missing !== null ? t.values.acked(acknowledged, missing) : '';
    }
    default:
      return '';
  }
}

/** "479 too low, 13 already leading": rejections by reason, as the bots heard them. */
export function rejectedLine(rejected: Record<string, number>): string {
  return Object.entries(rejected)
    .map(([code, n]) => `${n} ${(t.reasons as Record<string, string | undefined>)[code] ?? code.toLowerCase().replaceAll('_', ' ')}`)
    .join(', ');
}
