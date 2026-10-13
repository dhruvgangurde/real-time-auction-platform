// The one path into a room (brief 11.2): every REST snapshot, poll, refetch, socket frame and
// REST bid response becomes a RoomAction through dispatch(). The controller runs the reducer's
// effects (refetch, gap check, bid retry, rate-limit timer), polls /live every 2 s when this tab
// does not own the socket (item 3), and sends bids over the socket or over REST with the same
// request_id rules (items 5, 13, 14). It exposes subscribe/getState for useSyncExternalStore.
import type { Api, ApiError } from '../api/client';
import { newRequestId, type BidRejected, type InboundFrame, type ProxyRejected } from '../lib/contract';
import { initialRoom, roomReducer, type RoomAction, type RoomEffect, type RoomState, type Connection } from './room';
import type { Socket, Timers } from './socket';

export const POLL_MS = 2_000;
const RETRY_WAIT_MS = 500;
const MAX_SOCKET_RETRIES = 6;

export type RoomDeps = {
  auctionId: string;
  api: Pick<Api, 'live' | 'placeBid' | 'setProxy'>;
  /** the socket when this tab owns it; null means polling mode */
  socket: Socket | null;
  timers: Timers;
  /** document.hidden: polling pauses while the tab is hidden (brief 11.5) */
  hidden: () => boolean;
  requestId?: () => string;
  /** the state to start from (a switch to polling keeps what the room showed) */
  initial?: RoomState;
};

export type RoomController = ReturnType<typeof createRoomController>;

export function createRoomController(deps: RoomDeps) {
  let state = deps.initial ?? initialRoom(deps.auctionId, deps.socket === null ? 'polling' : 'reconnecting');
  const listeners = new Set<() => void>();
  const log: RoomAction[] = [];
  let refetching: Promise<void> | null = null;
  let pollTimer: unknown = null;
  let stopped = false;
  const retries = new Map<string, number>();
  const requestId = deps.requestId ?? newRequestId;

  function emit(): void {
    for (const listen of listeners) listen();
  }

  function dispatch(action: RoomAction): void {
    log.push(action);
    const step = roomReducer(state, action);
    state = step.state;
    emit();
    for (const effect of step.effects) run(effect);
  }

  function run(effect: RoomEffect): void {
    switch (effect.type) {
      case 'refetch':
        void refetch();
        return;
      case 'gapCheck':
        deps.timers.setTimeout(() => dispatch({ type: 'gapTimeout' }), effect.afterMs);
        return;
      case 'rateLimitTimer':
        deps.timers.setTimeout(() => dispatch({ type: 'rateLimitOver' }), effect.ms);
        return;
      case 'retryBid': {
        const pending = state.pending;
        if (pending === null || pending.requestId !== effect.requestId) return;
        const n = (retries.get(effect.requestId) ?? 0) + 1;
        retries.set(effect.requestId, n);
        if (n > MAX_SOCKET_RETRIES) {
          dispatch({ type: 'frame', frame: rejected(pending.kind, effect.requestId, 'SERVICE_UNAVAILABLE', null) });
          return;
        }
        deps.timers.setTimeout(() => void submit(pending.kind, pending.amount, effect.requestId), RETRY_WAIT_MS);
        return;
      }
    }
  }

  /** One refetch at a time; concurrent requests share it (item 7, 8, 9). */
  function refetch(): Promise<void> {
    if (refetching === null) {
      refetching = deps.api
        .live(deps.auctionId)
        .then((body) => dispatch({ type: 'snapshot', source: 'fetch', body }))
        .catch(() => undefined)
        .finally(() => {
          refetching = null;
        });
    }
    return refetching;
  }

  function schedulePoll(): void {
    if (stopped || deps.socket !== null) return;
    pollTimer = deps.timers.setTimeout(() => {
      pollTimer = null;
      if (deps.hidden()) {
        schedulePoll(); // paused while hidden: nothing fetched, try again later
        return;
      }
      void deps.api
        .live(deps.auctionId)
        .then((body) => dispatch({ type: 'snapshot', source: 'poll', body }))
        .catch(() => undefined)
        .finally(schedulePoll);
    }, POLL_MS);
  }

  function rejected(kind: 'bid' | 'proxy', id: string, code: string, minNext: number | null): BidRejected | ProxyRejected {
    const base = { request_id: id, auction_id: deps.auctionId, code, message: code, server_time_ms: 0 };
    const frame: BidRejected | ProxyRejected =
      kind === 'bid' ? { type: 'bid_rejected', ...base } : { type: 'proxy_rejected', ...base };
    if (minNext !== null) frame.min_next = minNext;
    return frame;
  }

  /** Over the socket when this tab owns it and it is open; otherwise over REST (item 5). */
  async function submit(kind: 'bid' | 'proxy', amount: number, id: string): Promise<void> {
    const socket = deps.socket;
    if (socket !== null) {
      const sent =
        kind === 'bid'
          ? socket.send({ type: 'place_bid', auction_id: deps.auctionId, amount, request_id: id })
          : socket.send({ type: 'set_proxy', auction_id: deps.auctionId, max_amount: amount, request_id: id });
      if (sent) return;
    }
    try {
      const ack = kind === 'bid' ? await deps.api.placeBid(deps.auctionId, amount, id) : await deps.api.setProxy(deps.auctionId, amount, id);
      dispatch({ type: 'frame', frame: ack });
    } catch (error) {
      const e = error as Partial<ApiError>;
      const minNext = /minimum next bid (\d+)/.exec(e.message ?? '')?.[1];
      dispatch({ type: 'frame', frame: rejected(kind, id, e.code ?? 'SERVICE_UNAVAILABLE', minNext ? Number(minNext) : null) });
    }
  }

  return {
    dispatch,
    getState: () => state,
    subscribe(listen: () => void): () => void {
      listeners.add(listen);
      return () => listeners.delete(listen);
    },
    /** every action dispatched, for tests */
    actions: log,

    /** The initial snapshot, then the socket subscription or the 2 s poll. */
    async start(): Promise<void> {
      if (deps.socket !== null) {
        dispatch({ type: 'subscribing' });
        deps.socket.subscribe(deps.auctionId);
      }
      await refetch();
      schedulePoll();
    },

    /** A frame from the socket; the caller routes by auction_id (system_status goes to all). */
    handleFrame(frame: InboundFrame): void {
      if (frame.type === 'subscribed') {
        if (frame.auction_id !== deps.auctionId) return;
        dispatch({ type: 'snapshot', source: 'subscribed', body: frame.snapshot, yourAlias: frame.your_alias });
        return;
      }
      if ('auction_id' in frame && frame.auction_id !== null && frame.auction_id !== deps.auctionId) return;
      dispatch({ type: 'frame', frame });
    },

    /** After a reconnect (socket.ts onResync): resubscribed already; refetch before events. */
    resync(): Promise<void> {
      dispatch({ type: 'subscribing' });
      return refetch();
    },

    /** closedCode: a terminal socket close (4004 signed out, 4012 another tab), for the banner */
    setConnection(connection: Connection, closedCode: number | null = null): void {
      dispatch({ type: 'connection', state: connection, closedCode });
    },

    placeBid(amount: number): string | null {
      const id = requestId();
      const before = state;
      dispatch({ type: 'bidSent', requestId: id, amount, kind: 'bid' });
      if (state.pending === null || state.pending.requestId !== id) return null; // refused (item 16)
      void submit('bid', amount, id);
      return before === state ? null : id;
    },

    setProxy(maxAmount: number): string {
      const id = requestId();
      dispatch({ type: 'bidSent', requestId: id, amount: maxAmount, kind: 'proxy' });
      void submit('proxy', maxAmount, id);
      return id;
    },

    stop(): void {
      stopped = true;
      if (pollTimer !== null) deps.timers.clearTimeout(pollTimer);
      deps.socket?.unsubscribe(deps.auctionId);
    },
  };
}
