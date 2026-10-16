// The audit log (brief 9.9; FR-77, FR-80): the filters in the URL, bounded as the API bounds them
// (admin.py audit_log), and an entry's before and after as a diff a person can read. A reserve
// amount is never shown, though the API sends it to administrators (Q2 of the slice 4 plan, BR-6,
// gap A3): "hidden", or "none" when there is none.
import { toUtcIso } from '../lib/clock';
import { formatINR } from '../lib/money';
import { adminEnIN } from '../strings/admin-en-IN';

export const TYPES = ['auction', 'user', 'category', 'settlement', 'payment', 'bid'] as const;
export type EntityType = (typeof TYPES)[number];
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const LIMIT = 50;

export function auditQuery(search: URLSearchParams) {
  const actor = UUID.test(search.get('actor') ?? '') ? (search.get('actor') as string) : '';
  const type = (TYPES as readonly string[]).includes(search.get('type') ?? '') ? (search.get('type') as EntityType) : '';
  const id = (search.get('id') ?? '').trim().slice(0, 200);
  const time = (name: 'from' | 'to') => {
    const value = search.get(name) ?? '';
    return Number.isNaN(new Date(value).getTime()) || value === '' ? '' : value;
  };
  const from = time('from');
  const to = time('to');
  const api = new URLSearchParams();
  if (actor !== '') api.set('actor_id', actor);
  if (type !== '') api.set('entity_type', type);
  if (id !== '') api.set('entity_id', id);
  if (from !== '') api.set('from', toUtcIso(from));
  if (to !== '') api.set('to', toUtcIso(to));
  api.set('limit', String(LIMIT));
  return { actor, type, id, from, to, api: api.toString() };
}

const MONEY = new Set(['starting_price', 'start_price', 'min_increment', 'current_price', 'final_price', 'amount']);
const HIDDEN = new Set(['reserve_price']);
const TIMES = new Set(['start_time', 'end_time', 'original_end_time', 'end_time_after', 'paid_at', 'created_at']);

function shown(key: string, value: unknown, at: (ms: number) => string): string {
  const a = adminEnIN.audit;
  if (HIDDEN.has(key)) return value === null ? a.none : a.hidden; // never the amount (BR-6)
  if (value === null) return a.none;
  if (typeof value === 'number' && MONEY.has(key)) return formatINR(value);
  if (TIMES.has(key) || key.endsWith('_ms')) {
    const ms = Number(value);
    if (Number.isSafeInteger(ms)) return at(ms);
  }
  if (typeof value === 'boolean') return value ? a.yes : a.no;
  if (typeof value === 'string' || typeof value === 'number') return String(value);
  return JSON.stringify(value);
}

type Values = Record<string, unknown> | null;

/** The keys that changed, or every key of a creation or a removal. */
export function diffRows(before: Values, after: Values, at: (ms: number) => string): { key: string; before: string; after: string }[] {
  const b = before ?? {};
  const a = after ?? {};
  const keys = [...new Set([...Object.keys(b), ...Object.keys(a)])];
  return keys
    .filter((key) => before === null || after === null || JSON.stringify(b[key]) !== JSON.stringify(a[key]))
    .map((key) => ({ key, before: key in b ? shown(key, b[key], at) : '', after: key in a ? shown(key, a[key], at) : '' }));
}
