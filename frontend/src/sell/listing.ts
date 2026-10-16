// The listing form's model (brief 9.8; FR-11, FR-12, FR-14, FR-17, BR-4, DC-6, OR-1). The form
// keeps what the seller typed as text; this checks it the way the server would (auctions.py
// _validate and the request schema), builds the create and edit bodies in paise and UTC, and maps
// the server's `fields` back onto the same inputs. The server still decides.
import type { AuctionDetail, AuctionStatus } from '../lib/contract';
import { toLocalInput, toUtcIso } from '../lib/clock';
import { formatINR, parseRupees } from '../lib/money';

export type ListingValues = {
  title: string;
  description: string;
  categoryId: string;
  startingPrice: string;
  minIncrement: string;
  reserve: string;
  start: string; // datetime-local, the viewer's zone
  end: string;
  threshold: string; // seconds
  extension: string;
};
export type Target = keyof ListingValues | 'images';
export type Problem =
  | 'required'
  | 'invalid'
  | 'amount'
  | 'startingMin'
  | 'incrementMin'
  | 'reserveBelowStart'
  | 'past'
  | 'endBeforeStart'
  | 'seconds'
  | 'category'
  | 'images';
export type Problems = Partial<Record<Target, Problem>>;
/** all: draft or scheduled; descriptive: live with no bid (title, description, images); none */
export type Editable = 'all' | 'descriptive' | 'none';

export const EMPTY: ListingValues = {
  title: '',
  description: '',
  categoryId: '',
  startingPrice: '',
  minIncrement: '',
  reserve: '',
  start: '',
  end: '',
  threshold: '60', // FR-14 defaults
  extension: '120',
};

const STARTING_MIN = 100; // paise: the gateway's minimum charge (schemas/auctions.py)
const SECONDS = /^\d+$/;
const SECONDS_MIN = 10; // config.py antisnipe bounds
const SECONDS_MAX = 600;

const paise = (text: string) => parseRupees(text);
const time = (local: string) => new Date(local).getTime();
const rupees = (amount: number) => formatINR(amount).replace('₹', '');

/** checkStart: the server checks the start only when it changes; checkOrder: the form shows stored
 * times to the minute, so two it leaves alone may read as equal (the server checks the stored ones). */
export function validate(v: ListingValues, nowMs: number, { checkStart = true, checkOrder = true } = {}): Problems {
  const p: Problems = {};
  if (v.title.trim() === '') p.title = 'required';
  if (v.description.trim() === '') p.description = 'required';
  if (v.categoryId === '') p.categoryId = 'required';

  const start = paise(v.startingPrice);
  if (!start.ok) p.startingPrice = start.reason === 'empty' ? 'required' : 'amount';
  else if (start.paise < STARTING_MIN) p.startingPrice = 'startingMin';
  const increment = paise(v.minIncrement);
  if (!increment.ok) p.minIncrement = increment.reason === 'empty' ? 'required' : 'amount';
  else if (increment.paise < 1) p.minIncrement = 'incrementMin';
  if (v.reserve.trim() !== '') {
    const reserve = paise(v.reserve);
    if (!reserve.ok) p.reserve = 'amount';
    else if (reserve.paise < 1 || (start.ok && reserve.paise < start.paise)) p.reserve = 'reserveBelowStart';
  }

  const from = time(v.start);
  const to = time(v.end);
  if (v.start === '') p.start = 'required';
  else if (Number.isNaN(from)) p.start = 'invalid';
  else if (checkStart && from < nowMs) p.start = 'past';
  if (v.end === '') p.end = 'required';
  else if (Number.isNaN(to)) p.end = 'invalid';
  else if (checkOrder && !Number.isNaN(from) && to <= from) p.end = 'endBeforeStart';

  for (const name of ['threshold', 'extension'] as const) {
    const s = v[name].trim();
    if (!SECONDS.test(s) || Number(s) < SECONDS_MIN || Number(s) > SECONDS_MAX) p[name] = 'seconds';
  }
  return p;
}

/** Only for values that validate() passed. */
function body(v: ListingValues): Record<string, unknown> {
  const amount = (text: string) => {
    const parsed = paise(text);
    if (!parsed.ok) throw new RangeError('validate the listing first');
    return parsed.paise;
  };
  return {
    title: v.title.trim(),
    description: v.description.trim(),
    category_id: Number(v.categoryId),
    starting_price: amount(v.startingPrice),
    min_increment: amount(v.minIncrement),
    reserve_price: v.reserve.trim() === '' ? null : amount(v.reserve),
    start_time: toUtcIso(v.start),
    end_time: toUtcIso(v.end),
    antisnipe_threshold_s: Number(v.threshold.trim()),
    antisnipe_extension_s: Number(v.extension.trim()),
  };
}

/** A new draft: absent reserve means no reserve (FR-12). */
export function toCreate(v: ListingValues): Record<string, unknown> {
  const { reserve_price, ...rest } = body(v);
  return reserve_price === null ? rest : { ...rest, reserve_price };
}

/** Only the fields that changed; on a live auction only title and description (FR-17). */
export function toPatch(v: ListingValues, before: ListingValues, can: Editable): Record<string, unknown> {
  if (can === 'none') return {};
  const after = body(v);
  const old = body(before);
  const allowed = can === 'descriptive' ? new Set(['title', 'description']) : null;
  return Object.fromEntries(
    Object.entries(after).filter(([key, value]) => (allowed === null || allowed.has(key)) && value !== old[key]),
  );
}

/** What the seller may change now. "A bid exists" is the room's sequence, as the server reads it (D8). */
export function editable(status: AuctionStatus, sequenceNo: number): Editable {
  if (status === 'draft' || status === 'scheduled') return 'all';
  if (status === 'live' && sequenceNo === 0) return 'descriptive';
  return 'none';
}

/** The stored listing as form text; the reserve only reaches its own seller (BR-6). */
export function fromDetail(d: AuctionDetail): ListingValues {
  return {
    title: d.title,
    description: d.description,
    categoryId: d.category === null ? '' : String(d.category.id),
    startingPrice: rupees(d.start_price),
    minIncrement: rupees(d.min_increment),
    reserve: d.reserve_price === undefined || d.reserve_price === null ? '' : rupees(d.reserve_price),
    start: toLocalInput(d.start_time_ms),
    end: toLocalInput(d.end_time_ms),
    threshold: String(d.antisnipe_threshold_s),
    extension: String(d.antisnipe_extension_s),
  };
}

/** Why a draft cannot be published yet, said before asking the server: it needs a photo (FR-13). */
export function publishProblem(images: number): Problem | null {
  return images === 0 ? 'images' : null;
}

const SERVER: Record<string, [Target, Problem]> = {
  title: ['title', 'invalid'],
  description: ['description', 'invalid'],
  category_id: ['categoryId', 'category'],
  starting_price: ['startingPrice', 'startingMin'],
  min_increment: ['minIncrement', 'incrementMin'],
  reserve_price: ['reserve', 'reserveBelowStart'],
  start_time: ['start', 'past'],
  end_time: ['end', 'endBeforeStart'],
  antisnipe_threshold_s: ['threshold', 'seconds'],
  antisnipe_extension_s: ['extension', 'seconds'],
  images: ['images', 'images'],
};

/** The envelope's `fields` (FR-12) on the form's inputs; anything else stays the top message. */
export function fromServer(fields: Record<string, string>): Problems {
  const p: Problems = {};
  for (const name of Object.keys(fields)) {
    const hit = SERVER[name];
    if (hit !== undefined) p[hit[0]] = hit[1];
  }
  return p;
}
