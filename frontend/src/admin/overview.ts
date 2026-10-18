// FR-81's dashboard and NFR-8's counters (GET /api/v1/admin/overview), guarded like lib/contract.ts:
// a fresh object with only the declared fields, null for anything malformed, never a throw; and
// each figure said in words for the Overview screen.
import { parsed, type Api } from '../api/client';
import { adminEnIN } from '../strings/admin-en-IN';
import { auditEntry, type AuditEntry } from './api';

type Health = 'ok' | 'unreachable';
export type Overview = {
  server_time_ms: number;
  bids: { per_second: number[]; accepted_60s: number; max_per_second: number; rejected_60s: Record<string, number>; rejection_rate: number };
  sockets: { instance: string; sockets: number; dropped: number }[];
  queues: { celery_waiting: number; celery_unacked: number; outbox_backlog: number };
  reconciliation: { gaps_total: number; last_run: { at_ms: number; checked: number; gaps: number; regressions: number; failed: number } | null };
  errors: { per_minute: number[] };
  health: { postgres: Health; redis: Health; broker: Health };
  recent_audit: AuditEntry[];
};

type Obj = Record<string, unknown>;
const isObj = (v: unknown): v is Obj => typeof v === 'object' && v !== null && !Array.isArray(v);
const count = (v: unknown): v is number => typeof v === 'number' && Number.isSafeInteger(v) && v >= 0;
const counts = (v: unknown): v is number[] => Array.isArray(v) && v.every(count);
const health = (v: unknown): v is Health => v === 'ok' || v === 'unreachable';
function pick<K extends string>(v: unknown, keys: readonly K[]): Record<K, number> | null {
  if (!isObj(v)) return null;
  const out = {} as Record<K, number>;
  for (const k of keys) {
    const n = v[k];
    if (!count(n)) return null;
    out[k] = n;
  }
  return out;
}

export function parseOverview(v: unknown): Overview | null {
  if (!isObj(v) || !count(v.server_time_ms)) return null;
  const b = v.bids;
  if (!isObj(b) || !counts(b.per_second) || !count(b.accepted_60s) || !count(b.max_per_second) || !isObj(b.rejected_60s)) return null;
  if (typeof b.rejection_rate !== 'number' || !(b.rejection_rate >= 0 && b.rejection_rate <= 1)) return null;
  const rejected: Record<string, number> = {};
  for (const [code, n] of Object.entries(b.rejected_60s)) {
    if (!count(n)) return null;
    rejected[code] = n;
  }
  if (!Array.isArray(v.sockets)) return null;
  const sockets: Overview['sockets'] = [];
  for (const s of v.sockets) {
    const n = pick(s, ['sockets', 'dropped'] as const);
    if (n === null || typeof (s as Obj).instance !== 'string') return null;
    sockets.push({ instance: (s as Obj).instance as string, ...n });
  }
  const queues = pick(v.queues, ['celery_waiting', 'celery_unacked', 'outbox_backlog'] as const);
  const r = v.reconciliation;
  if (queues === null || !isObj(r) || !count(r.gaps_total)) return null;
  const last = r.last_run === null ? null : pick(r.last_run, ['at_ms', 'checked', 'gaps', 'regressions', 'failed'] as const);
  if (r.last_run !== null && last === null) return null;
  const h = v.health;
  if (!isObj(v.errors) || !counts(v.errors.per_minute) || !isObj(h) || !health(h.postgres) || !health(h.redis) || !health(h.broker)) return null;
  if (!Array.isArray(v.recent_audit)) return null;
  const audit = v.recent_audit.map(auditEntry);
  if (!audit.every((e): e is AuditEntry => e !== null)) return null;
  return {
    server_time_ms: v.server_time_ms,
    bids: { per_second: [...b.per_second], accepted_60s: b.accepted_60s, max_per_second: b.max_per_second, rejected_60s: rejected, rejection_rate: b.rejection_rate },
    sockets,
    queues,
    reconciliation: { gaps_total: r.gaps_total, last_run: last },
    errors: { per_minute: [...v.errors.per_minute] },
    health: { postgres: h.postgres, redis: h.redis, broker: h.broker },
    recent_audit: audit,
  };
}

/** FR-81: administrators only; the server answers 403 FORBIDDEN to anyone else. */
export async function overview(api: Api): Promise<Overview> {
  return parsed(await api.request('GET', '/admin/overview', { auth: 'required' }), parseOverview);
}

export type OverviewLines = {
  accepted: string;
  rejected: string;
  reasons: string;
  sockets: string[];
  queues: string[];
  reconciliation: string[];
  errors: string;
  health: { name: string; state: string; ok: boolean }[];
};

/** Every figure in words; `at` formats a time for the reader. */
export function overviewLines(o: Overview, at: (ms: number) => string): OverviewLines {
  const t = adminEnIN.overview;
  const refused = Object.values(o.bids.rejected_60s).reduce((a, n) => a + n, 0);
  const reasons = Object.entries(o.bids.rejected_60s)
    .sort(([a, x], [b, y]) => y - x || a.localeCompare(b))
    .map(([code, n]) => `${code} ${n}`)
    .join(', ');
  const last = o.reconciliation.last_run;
  return {
    accepted: t.accepted(o.bids.accepted_60s, o.bids.max_per_second),
    rejected: t.rejected(refused, `${Math.round(o.bids.rejection_rate * 100)}%`),
    reasons: reasons === '' ? '' : t.reasons(reasons),
    sockets: o.sockets.length === 0 ? [t.noInstances] : o.sockets.map((s) => t.instance(s.instance, s.sockets, s.dropped)),
    queues: [t.waiting(o.queues.celery_waiting), t.inFlight(o.queues.celery_unacked), t.backlog(o.queues.outbox_backlog)],
    reconciliation: [t.gapsTotal(o.reconciliation.gaps_total), last === null ? t.notRun : t.lastRun(at(last.at_ms), last.checked, last.gaps, last.regressions, last.failed)],
    errors: t.errorsLine(o.errors.per_minute.reduce((a, n) => a + n, 0)),
    health: (['postgres', 'redis', 'broker'] as const).map((k) => ({ name: t.names[k], state: o.health[k] === 'ok' ? t.ok : t.unreachable, ok: o.health[k] === 'ok' })),
  };
}
