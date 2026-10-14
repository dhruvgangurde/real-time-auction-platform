// The wire contract (NFR-33, brief 11.2): every REST response the room needs and every WebSocket
// frame, mirroring docs/architecture.md section 4 as updated by B1 (placed_at_ms, reserve_met,
// self_outbid_blocked, you_lead, your_max, your_result, auction_started). Hand-written guards
// (brief 13 item 28): unknown frame types are ignored, a frame missing a required field or
// carrying a wrongly typed one is dropped, and parsing never throws. Every guard builds a fresh
// object with only the declared keys, so a stray field never travels further than this file.
// No type here has a field for a reserve amount except the owner-only reserve_price on
// AuctionDetail; a maximum appears only in the owner's own proxy_armed and your_max.

export type AuctionStatus =
  | 'draft'
  | 'scheduled'
  | 'live'
  | 'closing'
  | 'closed_sold'
  | 'closed_no_sale'
  | 'cancelled';
export type Outcome = 'closed_sold' | 'closed_no_sale' | 'cancelled';
export type BidType = 'manual' | 'automatic';

export type RecentBid = {
  sequence_no: number;
  amount: number;
  bidder_alias: string;
  bid_type: BidType;
  /** Redis TIME of acceptance; null on a replay stored before the field existed */
  placed_at_ms: number | null;
};

export type ViewerFields = {
  you_lead: boolean;
  /** the viewer's own active maximum, nobody else's (FR-51) */
  your_max: number | null;
  your_result: 'won' | 'lost' | null;
};

export type PublicSnapshot = {
  auction_id: string;
  status: AuctionStatus;
  current_price: number | null;
  leader_alias: string | null;
  sequence_no: number;
  bid_count: number;
  start_price: number;
  min_increment: number;
  min_next: number;
  end_time_ms: number;
  extension_count: number;
  participants: number | null;
  recent_bids: RecentBid[];
  degraded: boolean;
  reserve_met: boolean | null;
  self_outbid_blocked: boolean;
  server_time_ms: number;
};

/** /live: the public body, plus the viewer's own fields when the request carried a token. */
export type LiveSnapshot = PublicSnapshot & { viewer: ViewerFields | null };

export type AuctionDetail = LiveSnapshot & {
  title: string;
  description: string;
  category: { id: number; name: string; slug: string; parent_id: number | null } | null;
  images: { id: number; url: string; position: number }[]; // auction_images.id is a bigint
  seller: { id: string; name: string | null };
  start_time_ms: number;
  original_end_time_ms: number;
  antisnipe_threshold_s: number;
  antisnipe_extension_s: number;
  has_reserve: boolean;
  /** the seller's own listing only (BR-6) */
  reserve_price?: number | null;
};

export type HistoryBid = RecentBid & { bidder_id?: string };
export type BidHistoryPage = { items: HistoryBid[]; next_before: number | null; degraded: boolean };

export type TokenResponse = { access_token: string; token_type: string; expires_in: number };
export type UserProfile = {
  id: string;
  email: string;
  full_name: string;
  avatar_url: string | null;
  status: string;
  roles: string[];
};

export type ErrorEnvelope = { error: { code: string; message: string; fields?: Record<string, string> } };

/** A card in the listing, the watchlist and the seller list (catalog.py _card): price, bids and end
 * time from the live state, status from the record. No field for a reserve (BR-6). */
export type AuctionCard = {
  id: string;
  title: string;
  status: AuctionStatus;
  category_id: number;
  starting_price: number;
  current_price: number | null;
  bid_count: number;
  start_time_ms: number;
  end_time_ms: number;
  thumbnail_url: string | null;
};
export type AuctionPage = { items: AuctionCard[]; page: number; page_size: number; has_more: boolean; server_time_ms: number };
export type Watchlist = { items: AuctionCard[]; server_time_ms: number };
export type Category = { id: number; name: string; slug: string; parent_id: number | null; children: Category[] };
export type NotificationItem = {
  id: number;
  type: string;
  auction_id: string | null;
  payload: Record<string, unknown>;
  read: boolean;
  created_at_ms: number;
};
export type NotificationPage = { items: NotificationItem[]; next_before: number | null };
export type EmailPreferences = Record<string, boolean>;

// ---------------------------------------------------------------- frames

type Stamped = { server_time_ms: number };

export type Authenticated = Stamped & { type: 'authenticated'; user_id: string; expires_at_ms: number };
export type Subscribed = Stamped & {
  type: 'subscribed';
  auction_id: string;
  your_alias: string;
  snapshot: LiveSnapshot;
};
export type Unsubscribed = Stamped & { type: 'unsubscribed'; auction_id: string };
export type Pong = Stamped & { type: 'pong' };
export type BidAccepted = Stamped & {
  type: 'bid_accepted';
  request_id: string;
  auction_id: string;
  sequence_no: number;
  amount: number;
  end_time_ms: number;
  extended: boolean;
  extension_count: number;
  placed_at_ms: number | null;
};
export type BidRejected = Stamped & {
  type: 'bid_rejected';
  request_id: string;
  auction_id: string;
  code: string;
  message: string;
  current_highest?: number;
  min_next?: number;
};
export type ProxyArmed = Stamped & {
  type: 'proxy_armed';
  request_id: string;
  auction_id: string;
  max_amount: number;
  active: boolean;
};
export type ProxyRejected = Stamped & {
  type: 'proxy_rejected';
  request_id: string;
  auction_id: string;
  code: string;
  message: string;
  min_next?: number;
};
export type ErrorFrame = Stamped & { type: 'error'; code: string; message: string; request_id?: string };
export type BidPlaced = Stamped & {
  type: 'bid_placed';
  auction_id: string;
  sequence_no: number;
  amount: number;
  bidder_alias: string;
  bid_type: BidType;
  end_time_ms: number;
  extended: boolean;
  extension_count: number;
  placed_at_ms: number | null;
  reserve_met: boolean | null;
};
export type TimerExtended = Stamped & {
  type: 'timer_extended';
  auction_id: string;
  sequence_no: number;
  end_time_ms: number;
  extension_count: number;
  final: boolean;
};
export type AuctionStarted = Stamped & {
  type: 'auction_started';
  auction_id: string;
  status: 'live';
  start_time_ms: number;
  end_time_ms: number;
  start_price: number;
  min_increment: number;
  min_next: number;
  sequence_no: number;
  extension_count: number;
};
export type AuctionClosed = Stamped & {
  type: 'auction_closed';
  auction_id: string;
  outcome: Outcome;
  final_price: number | null;
  reserve_met: boolean | null;
  final_sequence_no: number;
};
export type Presence = Stamped & {
  type: 'presence';
  auction_id: string;
  participants: number;
  sequence_no: number | null;
  end_time_ms: number | null;
};
export type OutbidNotice = Stamped & {
  type: 'outbid_notice';
  auction_id: string;
  current_price: number;
  sequence_no: number;
};
export type Notification = Stamped & {
  type: 'notification';
  id: string;
  notification_type: string;
  auction_id: string | null;
  payload: Record<string, unknown>;
  created_at_ms: number;
};
export type SystemStatus = Stamped & { type: 'system_status'; degraded: boolean };

export type InboundFrame =
  | Authenticated
  | Subscribed
  | Unsubscribed
  | Pong
  | BidAccepted
  | BidRejected
  | ProxyArmed
  | ProxyRejected
  | ErrorFrame
  | BidPlaced
  | TimerExtended
  | AuctionStarted
  | AuctionClosed
  | Presence
  | OutbidNotice
  | Notification
  | SystemStatus;

/** Room events: those that carry an auction_id and reach every subscriber. */
export type RoomFrame = BidPlaced | TimerExtended | AuctionStarted | AuctionClosed | Presence;

export type OutboundFrame =
  | { type: 'authenticate'; token: string }
  | { type: 'subscribe'; auction_id: string }
  | { type: 'unsubscribe'; auction_id: string }
  | { type: 'place_bid'; auction_id: string; amount: number; request_id: string }
  | { type: 'set_proxy'; auction_id: string; max_amount: number; request_id: string }
  | { type: 'ping' };

// ---------------------------------------------------------------- request ids (item 13)

export const REQUEST_ID = /^[A-Za-z0-9_-]{1,64}$/;

export function newRequestId(): string {
  return crypto.randomUUID();
}

// ---------------------------------------------------------------- guards

type Obj = Record<string, unknown>;
const isObj = (v: unknown): v is Obj => typeof v === 'object' && v !== null && !Array.isArray(v);
const isStr = (v: unknown): v is string => typeof v === 'string';
const isBool = (v: unknown): v is boolean => typeof v === 'boolean';
const isInt = (v: unknown): v is number => typeof v === 'number' && Number.isSafeInteger(v);
const isMoney = (v: unknown): v is number => isInt(v) && v >= 0;
const isNullOr =
  <T>(check: (v: unknown) => v is T) =>
  (v: unknown): v is T | null =>
    v === null || check(v);
const nullOrInt = isNullOr(isInt);
const nullOrMoney = isNullOr(isMoney);
const nullOrStr = isNullOr(isStr);
const nullOrBool = isNullOr(isBool);
const STATUSES = new Set<string>([
  'draft',
  'scheduled',
  'live',
  'closing',
  'closed_sold',
  'closed_no_sale',
  'cancelled',
]);
const isStatus = (v: unknown): v is AuctionStatus => isStr(v) && STATUSES.has(v);
const OUTCOMES = new Set<string>(['closed_sold', 'closed_no_sale', 'cancelled']);
const isOutcome = (v: unknown): v is Outcome => isStr(v) && OUTCOMES.has(v);
const isBidType = (v: unknown): v is BidType => v === 'manual' || v === 'automatic';

/** Optional in the absent sense: missing is fine, present must pass. */
function optional<T>(v: unknown, check: (v: unknown) => v is T): v is T | undefined {
  return v === undefined || check(v);
}

function recentBid(v: unknown): RecentBid | null {
  if (!isObj(v)) return null;
  const { sequence_no, amount, bidder_alias, bid_type, placed_at_ms } = v;
  if (!isInt(sequence_no) || !isMoney(amount) || !isStr(bidder_alias) || !isBidType(bid_type)) {
    return null;
  }
  if (!optional(placed_at_ms, nullOrInt)) return null;
  return { sequence_no, amount, bidder_alias, bid_type, placed_at_ms: placed_at_ms ?? null };
}

function viewerFields(v: Obj): ViewerFields | null | undefined {
  // undefined: malformed; null: not present (anonymous); otherwise the viewer's own fields
  const { you_lead, your_max, your_result } = v;
  if (you_lead === undefined && your_max === undefined && your_result === undefined) return null;
  if (!isBool(you_lead) || !nullOrMoney(your_max)) return undefined;
  if (your_result !== null && your_result !== 'won' && your_result !== 'lost') return undefined;
  return { you_lead, your_max, your_result };
}

export function parseLiveSnapshot(v: unknown): LiveSnapshot | null {
  if (!isObj(v)) return null;
  const s = v;
  if (
    !isStr(s.auction_id) ||
    !isStatus(s.status) ||
    !nullOrMoney(s.current_price) ||
    !nullOrStr(s.leader_alias) ||
    !isInt(s.sequence_no) ||
    !isInt(s.bid_count) ||
    !isMoney(s.start_price) ||
    !isMoney(s.min_increment) ||
    !isMoney(s.min_next) ||
    !isInt(s.end_time_ms) ||
    !isInt(s.extension_count) ||
    !nullOrInt(s.participants) ||
    !Array.isArray(s.recent_bids) ||
    !isBool(s.degraded) ||
    !nullOrBool(s.reserve_met) ||
    !isBool(s.self_outbid_blocked) ||
    !isInt(s.server_time_ms)
  ) {
    return null;
  }
  const recent: RecentBid[] = [];
  for (const b of s.recent_bids) {
    const bid = recentBid(b);
    if (bid === null) return null;
    recent.push(bid);
  }
  const viewer = viewerFields(s);
  if (viewer === undefined) return null;
  return {
    auction_id: s.auction_id,
    status: s.status,
    current_price: s.current_price,
    leader_alias: s.leader_alias,
    sequence_no: s.sequence_no,
    bid_count: s.bid_count,
    start_price: s.start_price,
    min_increment: s.min_increment,
    min_next: s.min_next,
    end_time_ms: s.end_time_ms,
    extension_count: s.extension_count,
    participants: s.participants,
    recent_bids: recent,
    degraded: s.degraded,
    reserve_met: s.reserve_met,
    self_outbid_blocked: s.self_outbid_blocked,
    server_time_ms: s.server_time_ms,
    viewer,
  };
}

export function parseAuctionDetail(v: unknown): AuctionDetail | null {
  const snapshot = parseLiveSnapshot(v);
  if (snapshot === null || !isObj(v)) return null;
  const d = v;
  const category = d.category;
  if (
    !isStr(d.title) ||
    !isStr(d.description) ||
    !Array.isArray(d.images) ||
    !isObj(d.seller) ||
    !isStr(d.seller.id) ||
    !nullOrStr(d.seller.name) ||
    !isInt(d.start_time_ms) ||
    !isInt(d.original_end_time_ms) ||
    !isInt(d.antisnipe_threshold_s) ||
    !isInt(d.antisnipe_extension_s) ||
    !isBool(d.has_reserve) ||
    !optional(d.reserve_price, nullOrMoney)
  ) {
    return null;
  }
  if (category !== null) {
    if (!isObj(category) || !isInt(category.id) || !isStr(category.name) || !isStr(category.slug)) {
      return null;
    }
    if (!nullOrInt(category.parent_id)) return null;
  }
  const images: AuctionDetail['images'] = [];
  for (const i of d.images) {
    if (!isObj(i) || !isInt(i.id) || !isStr(i.url) || !isInt(i.position)) return null;
    images.push({ id: i.id, url: i.url, position: i.position });
  }
  const detail: AuctionDetail = {
    ...snapshot,
    title: d.title,
    description: d.description,
    category:
      category === null
        ? null
        : {
            id: (category as Obj).id as number,
            name: (category as Obj).name as string,
            slug: (category as Obj).slug as string,
            parent_id: (category as Obj).parent_id as number | null,
          },
    images,
    seller: { id: d.seller.id, name: d.seller.name },
    start_time_ms: d.start_time_ms,
    original_end_time_ms: d.original_end_time_ms,
    antisnipe_threshold_s: d.antisnipe_threshold_s,
    antisnipe_extension_s: d.antisnipe_extension_s,
    has_reserve: d.has_reserve,
  };
  if (d.reserve_price !== undefined) detail.reserve_price = d.reserve_price;
  return detail;
}

export function parseBidHistoryPage(v: unknown): BidHistoryPage | null {
  if (!isObj(v) || !Array.isArray(v.items) || !nullOrInt(v.next_before) || !isBool(v.degraded)) {
    return null;
  }
  const items: HistoryBid[] = [];
  for (const raw of v.items) {
    const bid = recentBid(raw);
    if (bid === null) return null;
    const bidder_id = isObj(raw) ? raw.bidder_id : undefined;
    if (!optional(bidder_id, isStr)) return null;
    items.push(bidder_id === undefined ? bid : { ...bid, bidder_id });
  }
  return { items, next_before: v.next_before, degraded: v.degraded };
}

export function parseTokenResponse(v: unknown): TokenResponse | null {
  if (!isObj(v) || !isStr(v.access_token) || !isStr(v.token_type) || !isInt(v.expires_in)) return null;
  return { access_token: v.access_token, token_type: v.token_type, expires_in: v.expires_in };
}

export function parseUserProfile(v: unknown): UserProfile | null {
  if (
    !isObj(v) ||
    !isStr(v.id) ||
    !isStr(v.email) ||
    !isStr(v.full_name) ||
    !nullOrStr(v.avatar_url) ||
    !isStr(v.status) ||
    !Array.isArray(v.roles) ||
    !v.roles.every(isStr)
  ) {
    return null;
  }
  return {
    id: v.id,
    email: v.email,
    full_name: v.full_name,
    avatar_url: v.avatar_url,
    status: v.status,
    roles: [...v.roles],
  };
}

export function parseErrorEnvelope(v: unknown): ErrorEnvelope | null {
  if (!isObj(v) || !isObj(v.error) || !isStr(v.error.code) || !isStr(v.error.message)) return null;
  const out: ErrorEnvelope = { error: { code: v.error.code, message: v.error.message } };
  if (isObj(v.error.fields)) {
    const fields: Record<string, string> = {};
    for (const [k, val] of Object.entries(v.error.fields)) if (isStr(val)) fields[k] = val;
    out.error.fields = fields;
  }
  return out;
}

// ---------------------------------------------------------------- slice 3 lists
// A malformed item is dropped and the rest of the list kept: one bad card never blanks a page.

function card(v: unknown): AuctionCard | null {
  if (!isObj(v)) return null;
  const { id, title, status, category_id, starting_price, current_price, bid_count, start_time_ms, end_time_ms, thumbnail_url } = v;
  if (
    !isStr(id) ||
    !isStr(title) ||
    !isStatus(status) ||
    !isInt(category_id) ||
    !isMoney(starting_price) ||
    !nullOrMoney(current_price) ||
    !isInt(bid_count) ||
    !isInt(start_time_ms) ||
    !isInt(end_time_ms) ||
    !nullOrStr(thumbnail_url)
  ) {
    return null;
  }
  return { id, title, status, category_id, starting_price, current_price, bid_count, start_time_ms, end_time_ms, thumbnail_url };
}

function each<T>(items: unknown[], parse: (v: unknown) => T | null): T[] {
  return items.map(parse).filter((x): x is T => x !== null);
}

export function parseAuctionPage(v: unknown): AuctionPage | null {
  if (!isObj(v) || !Array.isArray(v.items) || !isInt(v.page) || !isInt(v.page_size) || !isBool(v.has_more) || !isInt(v.server_time_ms)) {
    return null;
  }
  return { items: each(v.items, card), page: v.page, page_size: v.page_size, has_more: v.has_more, server_time_ms: v.server_time_ms };
}

export function parseWatchlist(v: unknown): Watchlist | null {
  if (!isObj(v) || !Array.isArray(v.items) || !isInt(v.server_time_ms)) return null;
  return { items: each(v.items, card), server_time_ms: v.server_time_ms };
}

function category(v: unknown): Category | null {
  if (!isObj(v) || !isInt(v.id) || !isStr(v.name) || !isStr(v.slug) || !nullOrInt(v.parent_id) || !Array.isArray(v.children)) {
    return null;
  }
  return { id: v.id, name: v.name, slug: v.slug, parent_id: v.parent_id, children: each(v.children, category) };
}

export function parseCategoryTree(v: unknown): Category[] | null {
  return Array.isArray(v) ? each(v, category) : null;
}

function notificationItem(v: unknown): NotificationItem | null {
  if (!isObj(v)) return null;
  const { id, type, auction_id, payload, read, created_at_ms } = v;
  if (!isInt(id) || !isStr(type) || !nullOrStr(auction_id) || !isObj(payload) || !isBool(read) || !isInt(created_at_ms)) return null;
  return { id, type, auction_id, payload: { ...payload }, read, created_at_ms };
}

export function parseNotificationPage(v: unknown): NotificationPage | null {
  if (!isObj(v) || !Array.isArray(v.items) || !nullOrInt(v.next_before)) return null;
  return { items: each(v.items, notificationItem), next_before: v.next_before };
}

export function parseEmailPreferences(v: unknown): EmailPreferences | null {
  if (!isObj(v) || !isObj(v.email)) return null;
  return Object.fromEntries(Object.entries(v.email).filter((e): e is [string, boolean] => isBool(e[1])));
}

// ---------------------------------------------------------------- frames

type FrameParser = (f: Obj, t: number) => InboundFrame | null;

const FRAMES: Record<InboundFrame['type'], FrameParser> = {
  authenticated: (f, t) =>
    isStr(f.user_id) && isInt(f.expires_at_ms)
      ? { type: 'authenticated', user_id: f.user_id, expires_at_ms: f.expires_at_ms, server_time_ms: t }
      : null,
  subscribed: (f, t) => {
    const snapshot = parseLiveSnapshot(f.snapshot);
    return isStr(f.auction_id) && isStr(f.your_alias) && snapshot !== null
      ? { type: 'subscribed', auction_id: f.auction_id, your_alias: f.your_alias, snapshot, server_time_ms: t }
      : null;
  },
  unsubscribed: (f, t) =>
    isStr(f.auction_id) ? { type: 'unsubscribed', auction_id: f.auction_id, server_time_ms: t } : null,
  pong: (_f, t) => ({ type: 'pong', server_time_ms: t }),
  bid_accepted: (f, t) =>
    isStr(f.request_id) &&
    isStr(f.auction_id) &&
    isInt(f.sequence_no) &&
    isMoney(f.amount) &&
    isInt(f.end_time_ms) &&
    isBool(f.extended) &&
    isInt(f.extension_count) &&
    optional(f.placed_at_ms, nullOrInt)
      ? {
          type: 'bid_accepted',
          request_id: f.request_id,
          auction_id: f.auction_id,
          sequence_no: f.sequence_no,
          amount: f.amount,
          end_time_ms: f.end_time_ms,
          extended: f.extended,
          extension_count: f.extension_count,
          placed_at_ms: f.placed_at_ms ?? null,
          server_time_ms: t,
        }
      : null,
  bid_rejected: (f, t) => {
    if (!isStr(f.request_id) || !isStr(f.auction_id) || !isStr(f.code) || !isStr(f.message)) return null;
    if (!optional(f.current_highest, isMoney) || !optional(f.min_next, isMoney)) return null;
    const out: BidRejected = {
      type: 'bid_rejected',
      request_id: f.request_id,
      auction_id: f.auction_id,
      code: f.code,
      message: f.message,
      server_time_ms: t,
    };
    if (f.current_highest !== undefined) out.current_highest = f.current_highest;
    if (f.min_next !== undefined) out.min_next = f.min_next;
    return out;
  },
  proxy_armed: (f, t) =>
    isStr(f.request_id) && isStr(f.auction_id) && isMoney(f.max_amount) && isBool(f.active)
      ? {
          type: 'proxy_armed',
          request_id: f.request_id,
          auction_id: f.auction_id,
          max_amount: f.max_amount,
          active: f.active,
          server_time_ms: t,
        }
      : null,
  proxy_rejected: (f, t) => {
    if (!isStr(f.request_id) || !isStr(f.auction_id) || !isStr(f.code) || !isStr(f.message)) return null;
    if (!optional(f.min_next, isMoney)) return null;
    const out: ProxyRejected = {
      type: 'proxy_rejected',
      request_id: f.request_id,
      auction_id: f.auction_id,
      code: f.code,
      message: f.message,
      server_time_ms: t,
    };
    if (f.min_next !== undefined) out.min_next = f.min_next;
    return out;
  },
  error: (f, t) => {
    if (!isStr(f.code) || !isStr(f.message) || !optional(f.request_id, nullOrStr)) return null;
    const out: ErrorFrame = { type: 'error', code: f.code, message: f.message, server_time_ms: t };
    if (isStr(f.request_id)) out.request_id = f.request_id;
    return out;
  },
  bid_placed: (f, t) =>
    isStr(f.auction_id) &&
    isInt(f.sequence_no) &&
    isMoney(f.amount) &&
    isStr(f.bidder_alias) &&
    isBidType(f.bid_type) &&
    isInt(f.end_time_ms) &&
    isBool(f.extended) &&
    isInt(f.extension_count) &&
    optional(f.placed_at_ms, nullOrInt) &&
    nullOrBool(f.reserve_met)
      ? {
          type: 'bid_placed',
          auction_id: f.auction_id,
          sequence_no: f.sequence_no,
          amount: f.amount,
          bidder_alias: f.bidder_alias,
          bid_type: f.bid_type,
          end_time_ms: f.end_time_ms,
          extended: f.extended,
          extension_count: f.extension_count,
          placed_at_ms: f.placed_at_ms ?? null,
          reserve_met: f.reserve_met,
          server_time_ms: t,
        }
      : null,
  timer_extended: (f, t) =>
    isStr(f.auction_id) &&
    isInt(f.sequence_no) &&
    isInt(f.end_time_ms) &&
    isInt(f.extension_count) &&
    isBool(f.final)
      ? {
          type: 'timer_extended',
          auction_id: f.auction_id,
          sequence_no: f.sequence_no,
          end_time_ms: f.end_time_ms,
          extension_count: f.extension_count,
          final: f.final,
          server_time_ms: t,
        }
      : null,
  auction_started: (f, t) =>
    isStr(f.auction_id) &&
    f.status === 'live' &&
    isInt(f.start_time_ms) &&
    isInt(f.end_time_ms) &&
    isMoney(f.start_price) &&
    isMoney(f.min_increment) &&
    isMoney(f.min_next) &&
    isInt(f.sequence_no) &&
    isInt(f.extension_count)
      ? {
          type: 'auction_started',
          auction_id: f.auction_id,
          status: 'live',
          start_time_ms: f.start_time_ms,
          end_time_ms: f.end_time_ms,
          start_price: f.start_price,
          min_increment: f.min_increment,
          min_next: f.min_next,
          sequence_no: f.sequence_no,
          extension_count: f.extension_count,
          server_time_ms: t,
        }
      : null,
  auction_closed: (f, t) =>
    isStr(f.auction_id) &&
    isOutcome(f.outcome) &&
    nullOrMoney(f.final_price) &&
    nullOrBool(f.reserve_met) &&
    isInt(f.final_sequence_no)
      ? {
          type: 'auction_closed',
          auction_id: f.auction_id,
          outcome: f.outcome,
          final_price: f.final_price,
          reserve_met: f.reserve_met,
          final_sequence_no: f.final_sequence_no,
          server_time_ms: t,
        }
      : null,
  presence: (f, t) =>
    isStr(f.auction_id) && isInt(f.participants) && nullOrInt(f.sequence_no) && nullOrInt(f.end_time_ms)
      ? {
          type: 'presence',
          auction_id: f.auction_id,
          participants: f.participants,
          sequence_no: f.sequence_no,
          end_time_ms: f.end_time_ms,
          server_time_ms: t,
        }
      : null,
  outbid_notice: (f, t) =>
    isStr(f.auction_id) && isMoney(f.current_price) && isInt(f.sequence_no)
      ? {
          type: 'outbid_notice',
          auction_id: f.auction_id,
          current_price: f.current_price,
          sequence_no: f.sequence_no,
          server_time_ms: t,
        }
      : null,
  notification: (f, t) =>
    isStr(f.id) &&
    isStr(f.notification_type) &&
    nullOrStr(f.auction_id) &&
    isObj(f.payload) &&
    isInt(f.created_at_ms)
      ? {
          type: 'notification',
          id: f.id,
          notification_type: f.notification_type,
          auction_id: f.auction_id,
          payload: { ...f.payload },
          created_at_ms: f.created_at_ms,
          server_time_ms: t,
        }
      : null,
  system_status: (f, t) =>
    isBool(f.degraded) ? { type: 'system_status', degraded: f.degraded, server_time_ms: t } : null,
};

/** A frame from the socket, or null for anything unknown or malformed; never throws. */
export function parseFrame(raw: unknown): InboundFrame | null {
  let value: unknown = raw;
  if (isStr(raw)) {
    try {
      value = JSON.parse(raw);
    } catch {
      return null;
    }
  }
  if (!isObj(value) || !isStr(value.type) || !isInt(value.server_time_ms)) return null;
  const parser = Object.hasOwn(FRAMES, value.type) ? FRAMES[value.type as InboundFrame['type']] : null;
  return parser === null ? null : parser(value, value.server_time_ms);
}

export function isRoomFrame(frame: InboundFrame): frame is RoomFrame {
  return (
    frame.type === 'bid_placed' ||
    frame.type === 'timer_extended' ||
    frame.type === 'auction_started' ||
    frame.type === 'auction_closed' ||
    frame.type === 'presence'
  );
}
