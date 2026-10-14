// Discover's filters live in the URL, so reload, back and forward and a shared link all show the
// same list (brief 9.2). The codec reads any query string, dropping what it cannot use (a
// hand-edited link never reaches a 422), writes the filters back with the defaults left out, and
// builds the GET /auctions query (FR-21 to FR-23). Prices are whole rupees in the URL and integer
// paise to the API (DC-6); "closing before" is a UTC instant (OR-1). The default status is live
// and closing, so "Live auctions" leads with live lots; scheduled is an opt-in tick.
import { toUtcIso } from '../lib/clock';

export const STATUS_CHOICES = ['scheduled', 'live', 'closing', 'closed_sold', 'closed_no_sale'] as const;
export type ListedStatus = (typeof STATUS_CHOICES)[number];

export type Filters = {
  q: string;
  /** a category slug (FR-20); mapped to its id through the category tree */
  category: string | null;
  /** never empty: DEFAULT_STATUS when the URL names none */
  status: ListedStatus[];
  /** whole rupees */
  min: number | null;
  max: number | null;
  /** UTC ISO instant */
  closingBefore: string | null;
  /** how many pages "Show more" has loaded */
  pages: number;
};

export const DEFAULT_STATUS: readonly ListedStatus[] = ['live', 'closing'];
export const EMPTY: Filters = { q: '', category: null, status: [...DEFAULT_STATUS], min: null, max: null, closingBefore: null, pages: 1 };
export const PAGE_SIZE = 20;
export const MAX_PAGES = 25;
export const MAX_QUERY = 200; // FR-22

const SLUG = /^[a-z0-9]+(?:-[a-z0-9]+)*$/; // schemas/auctions.py Slug
const RUPEES = /^\d{1,14}$/;
const INSTANT = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,3})?)?Z$/;

function rupees(v: string | null): number | null {
  if (v === null || !RUPEES.test(v)) return null;
  const n = Number(v);
  return Number.isSafeInteger(n * 100) ? n : null;
}

function instant(v: string | null): string | null {
  if (v === null || !INSTANT.test(v)) return null;
  const t = new Date(v).getTime();
  return Number.isNaN(t) ? null : new Date(t).toISOString();
}

/** The chosen statuses in a fixed order, or the default when none is chosen. */
export function statusOrDefault(chosen: readonly string[]): ListedStatus[] {
  const picked = STATUS_CHOICES.filter((s) => chosen.includes(s));
  return picked.length === 0 ? [...DEFAULT_STATUS] : picked;
}

/** Whether the statuses are the default set, in any order (then the URL leaves them out). */
export function isDefaultStatus(status: readonly ListedStatus[]): boolean {
  return status.length === DEFAULT_STATUS.length && DEFAULT_STATUS.every((s) => status.includes(s));
}

export function parseFilters(search: URLSearchParams): Filters {
  const category = search.get('category');
  const page = search.get('page');
  return {
    q: (search.get('q') ?? '').trim().slice(0, MAX_QUERY),
    category: category !== null && category.length <= 100 && SLUG.test(category) ? category : null,
    status: statusOrDefault(search.getAll('status')),
    min: rupees(search.get('min')),
    max: rupees(search.get('max')),
    closingBefore: instant(search.get('closing_before')),
    pages: page !== null && /^\d{1,4}$/.test(page) && Number(page) >= 1 ? Math.min(Number(page), MAX_PAGES) : 1,
  };
}

export function toSearch(f: Filters): URLSearchParams {
  const out = new URLSearchParams();
  if (f.q !== '') out.set('q', f.q);
  if (f.category !== null) out.set('category', f.category);
  if (!isDefaultStatus(f.status)) for (const s of f.status) out.append('status', s);
  if (f.min !== null) out.set('min', String(f.min));
  if (f.max !== null) out.set('max', String(f.max));
  if (f.closingBefore !== null) out.set('closing_before', f.closingBefore);
  if (f.pages > 1) out.set('page', String(f.pages));
  return out;
}

/** GET /auctions for one page; `categoryId` is the slug's id, null for no category filter. */
export function apiQuery(f: Filters, categoryId: number | null, page: number): URLSearchParams {
  const out = new URLSearchParams();
  if (f.q !== '') out.set('q', f.q);
  if (categoryId !== null) out.set('category', String(categoryId));
  for (const s of statusOrDefault(f.status)) out.append('status', s); // always explicit: the server's default has scheduled
  if (f.min !== null) out.set('min_price', String(f.min * 100));
  if (f.max !== null) out.set('max_price', String(f.max * 100));
  if (f.closingBefore !== null) out.set('closing_before', f.closingBefore);
  out.set('page', String(page));
  out.set('page_size', String(PAGE_SIZE));
  return out;
}

/** FR-23: the server refuses a minimum above the maximum; the form says so before asking. */
export function rangeError(f: Filters): boolean {
  return f.min !== null && f.max !== null && f.min > f.max;
}

/** A datetime-local value (the viewer's zone) as a UTC instant, or null when empty or invalid. */
export function fromLocalInput(value: string): string | null {
  try {
    return value === '' ? null : toUtcIso(value);
  } catch {
    return null;
  }
}

/** A UTC instant as a datetime-local value in the viewer's zone. */
export function toLocalInput(iso: string): string {
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
