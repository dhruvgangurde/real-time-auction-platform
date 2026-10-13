// The room reducer: one pure function per room (brief 11.2, section 13 items 6 to 19, 27, and
// the B1 client rules in architecture.md section 4). Every input, REST snapshot or socket frame,
// arrives as an action through roomController.dispatch; the reducer returns the next state and
// the effects the controller must run (refetch, a gap check, a bid retry, a rate-limit timer).
// It never reads the clock, never talks to the network and never trusts an alias for leading.
import type {
  AuctionStatus,
  BidPlaced,
  InboundFrame,
  LiveSnapshot,
  Outcome,
  RecentBid,
  RoomFrame,
  TimerExtended,
  ViewerFields,
} from '../lib/contract';

export const GAP_WAIT_MS = 300; // item 7
export const RATE_LIMIT_MS = 1_000; // FR-44: 10 per second per user
export const FINAL_EXTENSION = 10; // FR-54
const RECENT = 20;

export type Connection = 'offline' | 'reconnecting' | 'connected' | 'reconnected' | 'polling';
export type Pending = { requestId: string; amount: number; kind: 'bid' | 'proxy' };
export type LastResult =
  | { kind: 'accepted'; sequence: number; amount: number }
  | { kind: 'armed'; maximum: number; active: boolean }
  | { kind: 'rejected'; code: string; minNext: number | null; what: 'bid' | 'proxy' };

export type RoomState = {
  auctionId: string;
  status: AuctionStatus | null;
  /** sticky once set (item 10): no later frame reopens bidding */
  terminal: Outcome | null;
  sequence: number;
  price: number | null;
  leaderAlias: string | null;
  minNext: number | null;
  startPrice: number | null;
  minIncrement: number | null;
  endTimeMs: number | null;
  extensionCount: number;
  finalExtension: boolean;
  recentBids: RecentBid[];
  reserveMet: boolean | null;
  selfOutbidBlocked: boolean;
  participants: number | null;
  finalPrice: number | null;
  degraded: boolean;
  connection: Connection;
  /** the socket's subscribed snapshot for this room has been applied (item 6) */
  subscribed: boolean;
  buffer: RoomFrame[];
  gapPending: boolean;
  /** the end before the latest extension, for the timeline's "was" mark and the announcement */
  previousEndMs: number | null;
  /** a terminal socket close (4004 signed out, 4012 another tab), for the banner */
  closedCode: number | null;
  yourAlias: string | null;
  isSeller: boolean;
  own: {
    leading: boolean;
    /** sequences of the viewer's own accepted bids, from acks only (item 15) */
    sequences: number[];
    outbidAt: number | null;
    maximum: number | null;
    maximumActive: boolean;
    result: 'won' | 'lost' | null;
  };
  pending: Pending | null;
  last: LastResult | null;
  rateLimited: boolean;
};

export type RoomAction =
  | { type: 'snapshot'; source: 'subscribed' | 'fetch' | 'poll'; body: LiveSnapshot; yourAlias?: string }
  | { type: 'frame'; frame: InboundFrame }
  | { type: 'subscribing' }
  | { type: 'bidSent'; requestId: string; amount: number; kind: 'bid' | 'proxy' }
  | { type: 'gapTimeout' }
  | { type: 'rateLimitOver' }
  | { type: 'connection'; state: Connection; closedCode?: number | null }
  /** the server-derived countdown reached zero: Closing until a snapshot or extension says otherwise */
  | { type: 'countdownZero' }
  | { type: 'seller'; isSeller: boolean };

export type RoomEffect =
  | { type: 'refetch'; reason: string }
  | { type: 'gapCheck'; afterMs: number }
  | { type: 'retryBid'; requestId: string }
  | { type: 'rateLimitTimer'; ms: number };

export type Step = { state: RoomState; effects: RoomEffect[] };

export function initialRoom(auctionId: string, connection: Connection = 'reconnecting'): RoomState {
  return {
    auctionId,
    status: null,
    terminal: null,
    sequence: 0,
    price: null,
    leaderAlias: null,
    minNext: null,
    startPrice: null,
    minIncrement: null,
    endTimeMs: null,
    extensionCount: 0,
    finalExtension: false,
    recentBids: [],
    reserveMet: null,
    selfOutbidBlocked: true,
    participants: null,
    finalPrice: null,
    degraded: false,
    connection,
    subscribed: false,
    buffer: [],
    gapPending: false,
    previousEndMs: null,
    closedCode: null,
    yourAlias: null,
    isSeller: false,
    own: { leading: false, sequences: [], outbidAt: null, maximum: null, maximumActive: false, result: null },
    pending: null,
    last: null,
    rateLimited: false,
  };
}

const RANK: Record<AuctionStatus, number> = {
  draft: 0,
  scheduled: 1,
  live: 2,
  closing: 3,
  closed_sold: 4,
  closed_no_sale: 4,
  cancelled: 4,
};
const TERMINAL = new Set<AuctionStatus>(['closed_sold', 'closed_no_sale', 'cancelled']);

// ---------------------------------------------------------------- selectors

/** The room is live, the viewer may bid, nothing is pending and the service is not degraded. */
export function canBid(s: RoomState): boolean {
  return (
    s.status === 'live' &&
    s.terminal === null &&
    !s.isSeller &&
    !s.degraded &&
    s.pending === null &&
    !s.rateLimited &&
    !(s.selfOutbidBlocked && s.own.leading)
  );
}

/** Stale data never looks live (brief 6): only a connected or polling room is current. */
export function isStale(s: RoomState): boolean {
  if (s.degraded) return true;
  if (s.connection === 'polling') return false;
  return !(s.connection === 'connected' || s.connection === 'reconnected') || !s.subscribed;
}

// ---------------------------------------------------------------- the reducer

export function roomReducer(state: RoomState, action: RoomAction): Step {
  switch (action.type) {
    case 'snapshot':
      return applySnapshot(state, action);
    case 'frame':
      return applyFrame(state, action.frame);
    case 'subscribing':
      return done({ ...state, subscribed: false });
    case 'bidSent':
      if (!canBid(state) && action.kind === 'bid') return done(state);
      return done({
        ...state,
        pending: { requestId: action.requestId, amount: action.amount, kind: action.kind },
        last: null,
      });
    case 'gapTimeout': {
      const next = { ...state, gapPending: false };
      const first = next.buffer[0];
      if (first !== undefined && seqOf(first) > next.sequence + 1) {
        return { state: next, effects: [{ type: 'refetch', reason: 'gap' }] };
      }
      return done(next);
    }
    case 'rateLimitOver':
      return done({ ...state, rateLimited: false });
    case 'connection':
      return done({ ...state, connection: action.state, closedCode: action.closedCode ?? null });
    case 'countdownZero':
      return done(state.status === 'live' && state.terminal === null ? { ...state, status: 'closing' } : state);
    case 'seller':
      return done({ ...state, isSeller: action.isSeller });
  }
}

function done(state: RoomState, effects: RoomEffect[] = []): Step {
  return { state, effects };
}

function outcomeOf(status: AuctionStatus): Outcome | null {
  return TERMINAL.has(status) ? (status as Outcome) : null;
}

function applySnapshot(state: RoomState, action: Extract<RoomAction, { type: 'snapshot' }>): Step {
  const body = action.body;
  if (body.auction_id !== state.auctionId) return done(state);
  // terminal is sticky (item 10): a later snapshot never reopens bidding
  if (state.terminal !== null && outcomeOf(body.status) === null) return done(state);
  // polling never goes backwards (B1): keep the higher sequence and the later status
  if (
    action.source !== 'subscribed' &&
    state.status !== null &&
    (body.sequence_no < state.sequence || RANK[body.status] < RANK[state.status])
  ) {
    return done(state);
  }
  // fix: an accepted result must not outlive the lead it reported (a fresh you_lead=false wins)
  const lostLead = body.viewer !== null && !body.viewer.you_lead && state.last?.kind === 'accepted';
  let next: RoomState = {
    ...state,
    last: lostLead ? null : state.last,
    status: body.status,
    terminal: state.terminal ?? outcomeOf(body.status),
    sequence: body.sequence_no,
    price: body.current_price,
    leaderAlias: body.leader_alias,
    minNext: body.min_next,
    startPrice: body.start_price,
    minIncrement: body.min_increment,
    endTimeMs: body.end_time_ms,
    extensionCount: body.extension_count,
    finalExtension: body.extension_count >= FINAL_EXTENSION,
    recentBids: body.recent_bids,
    reserveMet: body.reserve_met,
    selfOutbidBlocked: body.self_outbid_blocked,
    participants: body.participants,
    degraded: body.degraded,
    finalPrice: outcomeOf(body.status) === 'closed_sold' ? body.current_price : state.finalPrice,
    yourAlias: action.yourAlias ?? state.yourAlias,
    own: applyViewer(state.own, body.viewer),
    buffer: state.buffer.filter((f) => seqOf(f) > body.sequence_no),
    gapPending: false,
  };
  if (action.source === 'subscribed') next = { ...next, subscribed: true };
  // drain what was buffered above the snapshot (item 6), in order
  return drain(next, []);
}

function applyViewer(own: RoomState['own'], viewer: ViewerFields | null): RoomState['own'] {
  if (viewer === null) return own;
  return {
    ...own,
    leading: viewer.you_lead, // exact, from the server (item 15)
    maximum: viewer.your_max,
    maximumActive: viewer.your_max !== null,
    result: viewer.your_result,
    outbidAt: viewer.you_lead ? null : own.outbidAt,
  };
}

function seqOf(frame: RoomFrame): number {
  return frame.type === 'auction_closed' ? frame.final_sequence_no : (frame.sequence_no ?? 0);
}

function applyFrame(state: RoomState, frame: InboundFrame): Step {
  switch (frame.type) {
    case 'system_status': {
      // item 27: degraded disables bidding; item 8: a recovery refetches
      const next = { ...state, degraded: frame.degraded };
      return frame.degraded ? done(next) : done(next, [{ type: 'refetch', reason: 'recovered' }]);
    }
    case 'bid_accepted':
      return applyAccepted(state, frame);
    case 'bid_rejected':
    case 'proxy_rejected':
      return applyRejected(state, frame.request_id, frame.code, frame.min_next ?? null, frame.type === 'bid_rejected' ? 'bid' : 'proxy');
    case 'error':
      return frame.request_id !== undefined && state.pending?.requestId === frame.request_id
        ? applyRejected(state, frame.request_id, frame.code, null, state.pending.kind)
        : done(state);
    case 'proxy_armed': {
      if (frame.auction_id !== state.auctionId) return done(state);
      const pending = state.pending?.requestId === frame.request_id ? null : state.pending;
      // proxy_armed carries no leading info (arch 4) and an alias never sets leading (item 15):
      // refetch /live (authenticated, so fresh you_lead) so an answering maximum clears the outbid
      return done(
        {
          ...state,
          pending,
          last: { kind: 'armed', maximum: frame.max_amount, active: frame.active },
          own: { ...state.own, maximum: frame.max_amount, maximumActive: frame.active },
        },
        [{ type: 'refetch', reason: 'proxy-armed' }],
      );
    }
    case 'outbid_notice':
      if (frame.auction_id !== state.auctionId) return done(state);
      // a maximum holder is told only when the trigger exhausted it (arch 4): the maximum is spent
      return done({ ...state, last: null, own: { ...state.own, leading: false, outbidAt: frame.current_price, maximumActive: false } });
    case 'bid_placed':
    case 'timer_extended':
    case 'auction_started':
    case 'auction_closed':
    case 'presence':
      return applyRoomFrame(state, frame);
    default:
      return done(state); // authenticated, subscribed, unsubscribed, pong, notification
  }
}

function applyAccepted(state: RoomState, frame: Extract<InboundFrame, { type: 'bid_accepted' }>): Step {
  if (frame.auction_id !== state.auctionId) return done(state);
  const pending = state.pending?.requestId === frame.request_id ? null : state.pending;
  const own = {
    ...state.own,
    leading: true, // from the ack (item 15)
    outbidAt: null,
    sequences: state.own.sequences.includes(frame.sequence_no)
      ? state.own.sequences
      : [...state.own.sequences, frame.sequence_no],
  };
  const acked: RoomState = { ...state, pending, own, last: { kind: 'accepted', sequence: frame.sequence_no, amount: frame.amount } };
  // the ack arrives before the broadcast (FR-40): apply it through the same sequence rule
  const asPlaced: BidPlaced = {
    type: 'bid_placed',
    auction_id: frame.auction_id,
    sequence_no: frame.sequence_no,
    amount: frame.amount,
    bidder_alias: state.yourAlias ?? '',
    bid_type: 'manual',
    end_time_ms: frame.end_time_ms,
    extended: frame.extended,
    extension_count: frame.extension_count,
    placed_at_ms: frame.placed_at_ms,
    reserve_met: state.reserveMet,
    server_time_ms: frame.server_time_ms,
  };
  return applyRoomFrame(acked, asPlaced);
}

function applyRejected(state: RoomState, requestId: string, code: string, minNext: number | null, what: 'bid' | 'proxy'): Step {
  if (state.pending?.requestId !== requestId) return done(state);
  if (code === 'SERVICE_UNAVAILABLE') {
    // item 14: keep the pending bid and retry with the same request_id
    return done({ ...state, last: { kind: 'rejected', code, minNext, what } }, [{ type: 'retryBid', requestId }]);
  }
  const next: RoomState = {
    ...state,
    pending: null,
    last: { kind: 'rejected', code, minNext, what },
    minNext: minNext ?? state.minNext,
  };
  if (code === 'RATE_LIMITED') {
    // item 16
    return done({ ...next, rateLimited: true }, [{ type: 'rateLimitTimer', ms: RATE_LIMIT_MS }]);
  }
  return done(next);
}

function applyRoomFrame(state: RoomState, frame: RoomFrame): Step {
  if (frame.auction_id !== state.auctionId) return done(state);
  if (frame.type === 'presence') return applyPresence(state, frame);
  if (frame.type === 'auction_closed') return applyClosed(state, frame);
  if (frame.type === 'auction_started') return applyStarted(state, frame);
  // bid_placed or timer_extended
  if (state.terminal !== null) return done(state); // item 10
  if (state.status === 'scheduled') {
    // B1: a room event while scheduled means the server is live; the refetch says so
    return done(state, [{ type: 'refetch', reason: 'event-while-scheduled' }]);
  }
  if (!state.subscribed && state.connection !== 'polling') {
    // item 6: buffer until the subscribed snapshot
    return done({ ...state, buffer: insert(state.buffer, frame) });
  }
  return drain({ ...state, buffer: insert(state.buffer, frame) }, []);
}

/** Apply every buffered frame that is next in sequence; hold a gap for 300 ms, then refetch. */
function drain(state: RoomState, effects: RoomEffect[]): Step {
  let next = state;
  for (;;) {
    const frame = next.buffer[0];
    if (frame === undefined) break;
    const seq = seqOf(frame);
    if (seq <= next.sequence) {
      // item 7: a repeat, except a timer_extended that shares its bid's sequence
      next = { ...next, buffer: next.buffer.slice(1) };
      if (frame.type === 'timer_extended' && seq === next.sequence) next = extend(next, frame);
      continue;
    }
    if (seq === next.sequence + 1 && frame.type === 'bid_placed') {
      next = { ...place(next, frame), buffer: next.buffer.slice(1) };
      continue;
    }
    // a gap, or a timer_extended that arrived before its bid: wait, then refetch
    if (!next.gapPending) {
      next = { ...next, gapPending: true };
      effects.push({ type: 'gapCheck', afterMs: GAP_WAIT_MS });
    }
    break;
  }
  return { state: next, effects };
}

function insert(buffer: RoomFrame[], frame: RoomFrame): RoomFrame[] {
  const key = (f: RoomFrame) => seqOf(f) * 2 + (f.type === 'timer_extended' ? 1 : 0);
  if (buffer.some((f) => f.type === frame.type && seqOf(f) === seqOf(frame))) return buffer; // (type, seq) dedupe
  const out = [...buffer, frame];
  out.sort((a, b) => key(a) - key(b));
  return out;
}

function place(state: RoomState, frame: BidPlaced): RoomState {
  const bid: RecentBid = {
    sequence_no: frame.sequence_no,
    amount: frame.amount,
    bidder_alias: frame.bidder_alias,
    bid_type: frame.bid_type,
    placed_at_ms: frame.placed_at_ms,
  };
  const increment = state.minIncrement ?? 0;
  const extended = state.endTimeMs !== null && frame.end_time_ms > state.endTimeMs;
  return {
    ...state,
    // a bid inside the last minute reopens a locally closing room (brief 9.3 "closing")
    status: extended && state.status === 'closing' ? 'live' : state.status,
    previousEndMs: extended ? state.endTimeMs : state.previousEndMs,
    sequence: frame.sequence_no,
    price: frame.amount,
    leaderAlias: frame.bidder_alias,
    minNext: frame.amount + increment,
    endTimeMs: frame.end_time_ms,
    extensionCount: frame.extension_count,
    finalExtension: state.finalExtension || frame.extension_count >= FINAL_EXTENSION,
    recentBids: [bid, ...state.recentBids.filter((b) => b.sequence_no !== bid.sequence_no)].slice(0, RECENT),
    reserveMet: frame.reserve_met,
  };
}

function extend(state: RoomState, frame: TimerExtended): RoomState {
  if (state.endTimeMs !== null && frame.end_time_ms <= state.endTimeMs && frame.extension_count <= state.extensionCount) {
    return state; // already applied through its bid
  }
  const extended = state.endTimeMs !== null && frame.end_time_ms > state.endTimeMs;
  return {
    ...state,
    status: extended && state.status === 'closing' ? 'live' : state.status,
    previousEndMs: extended ? state.endTimeMs : state.previousEndMs,
    endTimeMs: Math.max(state.endTimeMs ?? 0, frame.end_time_ms),
    extensionCount: Math.max(state.extensionCount, frame.extension_count),
    finalExtension: state.finalExtension || frame.final || frame.extension_count >= FINAL_EXTENSION,
  };
}

function applyPresence(state: RoomState, frame: Extract<RoomFrame, { type: 'presence' }>): Step {
  const next: RoomState = { ...state, participants: frame.participants };
  if (state.terminal !== null) return done(next);
  if (frame.sequence_no !== null && frame.sequence_no > state.sequence) {
    // item 8: a lost event in a quiet room; B1: a live sequence in a scheduled room
    return done(next, [{ type: 'refetch', reason: 'presence-ahead' }]);
  }
  if (frame.end_time_ms !== null && state.status === 'live' && state.endTimeMs !== null && frame.end_time_ms > state.endTimeMs) {
    return done({ ...next, endTimeMs: frame.end_time_ms });
  }
  return done(next);
}

function applyStarted(state: RoomState, frame: Extract<RoomFrame, { type: 'auction_started' }>): Step {
  // item 11 and B1: only the server takes a scheduled room live; the refetch brings the truth,
  // because a late auction_started from the repair pass carries sequence 0 and the original end
  if (state.terminal !== null || state.status === 'live' || state.status === 'closing') return done(state);
  const next: RoomState = {
    ...state,
    status: 'live',
    startPrice: frame.start_price,
    minIncrement: frame.min_increment,
    minNext: state.sequence === 0 ? frame.min_next : state.minNext,
    endTimeMs: state.endTimeMs ?? frame.end_time_ms,
  };
  return done(next, [{ type: 'refetch', reason: 'started' }]);
}

function applyClosed(state: RoomState, frame: Extract<RoomFrame, { type: 'auction_closed' }>): Step {
  // item 9: always applied, whatever its sequence; buffered bids up to final_sequence_no first
  const { state: drained, effects } = drain(state, []);
  const missed = drained.sequence < frame.final_sequence_no;
  const result =
    frame.outcome === 'closed_sold'
      ? drained.own.leading
        ? 'won'
        : drained.own.sequences.length > 0 || drained.own.maximum !== null
          ? 'lost'
          : drained.own.result
      : null;
  const next: RoomState = {
    ...drained,
    status: frame.outcome,
    terminal: frame.outcome,
    finalPrice: frame.final_price,
    reserveMet: frame.reserve_met,
    sequence: Math.max(drained.sequence, frame.final_sequence_no),
    buffer: [],
    gapPending: false,
    pending: null,
    own: { ...drained.own, result, maximumActive: false },
  };
  // the result and the final bids come from /live (your_result), at most once is not enough
  const refetch: RoomEffect[] = [{ type: 'refetch', reason: missed ? 'closed-gap' : 'closed' }];
  return { state: next, effects: [...effects.filter((e) => e.type !== 'gapCheck'), ...refetch] };
}
