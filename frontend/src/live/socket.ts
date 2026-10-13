// The WebSocket client (brief 13 items 1, 2, 4, 5; CI-4, CI-6, D3, D4). A connection state
// machine: Offline at once on the browser's offline event; Reconnecting when the socket closes or
// a subscribed room hears nothing for 12 s; Reconnected for 2 s only after re-authenticate,
// resubscribe and the resync hook (the controller's /live refetch); then Connected. Close codes:
// 4002, 4004 and 4012 never reconnect; 4003 refreshes the token first; 4010 and 4009 stop with a
// message; 1001, 1011, 4008 and drops reconnect with exponential backoff and jitter. The first
// frame is always authenticate; the URL never carries a token; no application ping is sent.
import { parseFrame, type InboundFrame, type OutboundFrame } from '../lib/contract';

export type SocketState = 'offline' | 'reconnecting' | 'connected' | 'reconnected';
export type SocketStatus =
  | { state: SocketState; closed: null }
  /** closed for good: the code says why (4002, 4004, 4012, 4009, 4010); no reconnect */
  | { state: 'offline'; closed: { code: number } };

export type WsLike = {
  readyState: number;
  send: (data: string) => void;
  close: (code?: number, reason?: string) => void;
  onopen: ((ev: unknown) => void) | null;
  onmessage: ((ev: { data: unknown }) => void) | null;
  onclose: ((ev: { code: number; reason: string }) => void) | null;
  onerror: ((ev: unknown) => void) | null;
};

export type Timers = {
  setTimeout: (fn: () => void, ms: number) => unknown;
  clearTimeout: (handle: unknown) => void;
};

export type SocketDeps = {
  url: string;
  open: (url: string) => WsLike;
  getToken: () => string | null;
  /** refresh the access token (auth.ts); null when the session is gone */
  refresh: () => Promise<string | null>;
  now: () => number;
  timers: Timers;
  random: () => number;
  online: () => boolean;
  onFrame: (frame: InboundFrame) => void;
  onStatus: (status: SocketStatus) => void;
  /** after a reconnect: the rooms are resubscribed; refetch /live before applying events */
  onResync: () => Promise<void>;
};

export const MAX_ROOMS = 20; // D5
export const SILENCE_MS = 12_000; // presence arrives every 5 s
export const RECONNECTED_MS = 2_000;
export const BACKOFF_MIN_MS = 500;
export const BACKOFF_MAX_MS = 30_000;
export const REAUTH_LEAD_MS = 60_000;
const NO_RECONNECT = new Set([4002, 4004, 4012]);
const STOP_WITH_MESSAGE = new Set([4009, 4010]);
const OPEN = 1;

export type Socket = ReturnType<typeof createSocket>;

export function createSocket(deps: SocketDeps) {
  const rooms = new Set<string>();
  let ws: WsLike | null = null;
  let status: SocketStatus = { state: 'offline', closed: null };
  let attempts = 0;
  let wasConnected = false; // a resync is only owed after a loss
  let reconnectTimer: unknown = null;
  let silenceTimer: unknown = null;
  let reauthTimer: unknown = null;
  let reconnectedTimer: unknown = null;
  let stopped = false;
  const pendingSubscribed = new Set<string>();

  function setStatus(next: SocketStatus): void {
    if (next.state === status.state && next.closed === status.closed) return;
    status = next;
    deps.onStatus(status);
  }

  function clear(handle: unknown): void {
    if (handle !== null) deps.timers.clearTimeout(handle);
  }

  function send(frame: OutboundFrame): boolean {
    if (ws === null || ws.readyState !== OPEN) return false;
    ws.send(JSON.stringify(frame));
    return true;
  }

  function armSilence(): void {
    clear(silenceTimer);
    silenceTimer = null;
    if (rooms.size === 0) return;
    silenceTimer = deps.timers.setTimeout(() => {
      // a subscribed room heard nothing for 12 s: treat the socket as lost
      if (ws !== null) ws.close(1000, 'silent');
      lost(1006);
    }, SILENCE_MS);
  }

  function scheduleReauth(expiresAtMs: number): void {
    clear(reauthTimer);
    const wait = Math.max(0, expiresAtMs - REAUTH_LEAD_MS - deps.now());
    reauthTimer = deps.timers.setTimeout(() => {
      void deps.refresh().then((token) => {
        if (token !== null) send({ type: 'authenticate', token });
      });
    }, wait);
  }

  function backoffMs(): number {
    const base = Math.min(BACKOFF_MAX_MS, BACKOFF_MIN_MS * 2 ** attempts);
    const jitter = (deps.random() - 0.5) * 0.5 * base; // plus or minus a quarter
    return Math.round(base + jitter);
  }

  function scheduleReconnect(): void {
    clear(reconnectTimer);
    const wait = backoffMs();
    attempts += 1;
    reconnectTimer = deps.timers.setTimeout(() => {
      reconnectTimer = null;
      void connect();
    }, wait);
  }

  function lost(code: number): void {
    const socket = ws;
    ws = null;
    if (socket !== null) {
      socket.onclose = null;
      socket.onmessage = null;
    }
    clear(silenceTimer);
    clear(reauthTimer);
    clear(reconnectedTimer);
    silenceTimer = reauthTimer = reconnectedTimer = null;
    pendingSubscribed.clear();
    if (stopped) return;
    if (NO_RECONNECT.has(code) || STOP_WITH_MESSAGE.has(code)) {
      setStatus({ state: 'offline', closed: { code } });
      return;
    }
    if (!deps.online()) {
      setStatus({ state: 'offline', closed: null });
      return;
    }
    setStatus({ state: 'reconnecting', closed: null });
    if (code === 4003) {
      // the access token expired: refresh first, then reconnect at once
      void deps.refresh().then((token) => {
        if (token !== null) void connect();
        else setStatus({ state: 'offline', closed: { code: 4003 } });
      });
      return;
    }
    scheduleReconnect();
  }

  async function connect(): Promise<void> {
    if (stopped || ws !== null) return;
    const token = deps.getToken();
    if (token === null) return; // anonymous viewers never open a socket (FR-28)
    if (!deps.online()) {
      setStatus({ state: 'offline', closed: null });
      return;
    }
    const socket = deps.open(deps.url); // no token in the URL (CI-4)
    ws = socket;
    socket.onopen = () => {
      send({ type: 'authenticate', token }); // the first frame, within 5 s
    };
    socket.onerror = () => undefined; // the close that follows carries the code
    socket.onclose = (ev) => {
      if (ws === socket) lost(ev.code);
    };
    socket.onmessage = (ev) => {
      const frame = parseFrame(ev.data); // malformed frames are dropped here (item 28)
      if (frame === null) return;
      if (frame.type === 'authenticated') {
        attempts = 0;
        scheduleReauth(frame.expires_at_ms);
        for (const auctionId of rooms) {
          pendingSubscribed.add(auctionId);
          send({ type: 'subscribe', auction_id: auctionId });
        }
        if (rooms.size === 0) settle();
      } else if (frame.type === 'subscribed') {
        pendingSubscribed.delete(frame.auction_id);
        if (pendingSubscribed.size === 0) settle();
      }
      if (frame.type !== 'authenticated') armSilence();
      deps.onFrame(frame);
    };
  }

  /** Authenticated and every room resubscribed: Connected, or Reconnected after a loss. */
  function settle(): void {
    if (!wasConnected) {
      wasConnected = true;
      setStatus({ state: 'connected', closed: null });
      armSilence();
      return;
    }
    void deps.onResync().then(() => {
      if (ws === null) return;
      setStatus({ state: 'reconnected', closed: null });
      armSilence();
      clear(reconnectedTimer);
      reconnectedTimer = deps.timers.setTimeout(() => {
        reconnectedTimer = null;
        if (ws !== null) setStatus({ state: 'connected', closed: null });
      }, RECONNECTED_MS);
    });
  }

  return {
    connect,
    get status(): SocketStatus {
      return status;
    },

    /** The browser went offline or came back. */
    setOnline(online: boolean): void {
      if (!online) {
        clear(reconnectTimer);
        reconnectTimer = null;
        if (ws !== null) ws.close(1000, 'offline');
        ws = null;
        clear(silenceTimer);
        silenceTimer = null;
        setStatus({ state: 'offline', closed: null });
        return;
      }
      if (ws === null && status.closed === null && !stopped) {
        attempts = 0;
        setStatus({ state: 'reconnecting', closed: null });
        void connect();
      }
    },

    subscribe(auctionId: string): boolean {
      if (rooms.has(auctionId)) return true;
      if (rooms.size >= MAX_ROOMS) return false; // D5: leave rooms you navigate away from
      rooms.add(auctionId);
      if (ws !== null && ws.readyState === OPEN) {
        pendingSubscribed.add(auctionId);
        send({ type: 'subscribe', auction_id: auctionId });
      }
      armSilence();
      return true;
    },

    unsubscribe(auctionId: string): void {
      if (!rooms.delete(auctionId)) return;
      pendingSubscribed.delete(auctionId);
      send({ type: 'unsubscribe', auction_id: auctionId });
      armSilence();
    },

    send,

    close(): void {
      stopped = true;
      clear(reconnectTimer);
      clear(silenceTimer);
      clear(reauthTimer);
      clear(reconnectedTimer);
      if (ws !== null) ws.close(1000, 'leaving');
      ws = null;
    },
  };
}

/** The browser's WebSocket, as WsLike. */
export function openBrowserSocket(url: string): WsLike {
  return new WebSocket(url) as unknown as WsLike;
}
