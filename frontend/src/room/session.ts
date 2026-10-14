// One room session per open room: the controller, the socket when this tab owns it (brief 13
// item 3), the 2 s poll otherwise, the server clock samples from every frame, and the live
// regions (announcer.ts) written into the shell's two sr-only elements. Framework-free and
// side-effect free until start(); the useRoom hook starts and stops it.
import type { Api } from '../api/client';
import type { Auth } from '../api/auth';
import type { ServerClock } from '../lib/clock';
import { formatLocal } from '../lib/clock';
import type { Catalogue } from '../strings/catalogue';
import { announce } from '../live/announcer';
import { initialRoom, isStale, type RoomState } from '../live/room';
import { createRoomController, type RoomController } from '../live/roomController';
import { createSocket, openBrowserSocket, type Socket } from '../live/socket';
import { browserLockRequest, createTabs, type Tabs } from '../live/tabs';

export type Mode = 'socket' | 'polling';

export type SessionDeps = {
  auctionId: string;
  api: Api;
  auth: Auth;
  clock: ServerClock;
  catalogue: Catalogue;
  /** the app's polite queue (lib/polite.ts), shared with the shell */
  polite: (text: string) => void;
  /** a stored notification arrived live on the user channel (FR-71): the bell reads again */
  onNotification?: () => void;
  assertiveRegion: () => HTMLElement | null;
};

export type RoomSession = ReturnType<typeof createRoomSession>;

const timers = {
  setTimeout: (fn: () => void, ms: number): unknown => setTimeout(fn, ms),
  clearTimeout: (h: unknown): void => clearTimeout(h as number),
};

export function createRoomSession(deps: SessionDeps) {
  const listeners = new Set<() => void>();
  const empty = initialRoom(deps.auctionId, 'polling');
  let mode: Mode = 'polling';
  let controller: RoomController | null = null;
  let socket: Socket | null = null;
  let tabs: Tabs | null = null;
  let stopped = true;
  let unsubscribe: (() => void) | null = null;
  let lastState: RoomState | null = null;
  /** server time of the last state that was current, for "Last update HH:MM:SS" (brief 6) */
  let lastUpdateMs: number | null = null;
  let windowMs: number | null = null;
  const time = (ms: number) => formatLocal(ms, { seconds: true });

  function write(region: HTMLElement | null, text: string): void {
    if (region === null) return;
    region.textContent = '';
    region.textContent = text;
  }

  function emit(): void {
    for (const listen of listeners) listen();
  }

  function attach(next: RoomController): void {
    unsubscribe?.();
    controller = next;
    lastState = next.getState();
    unsubscribe = next.subscribe(() => {
      const state = next.getState();
      if (!isStale(state) && state.status !== null) lastUpdateMs = deps.clock.now();
      if (lastState !== null) {
        const said = announce(lastState, state, { catalogue: deps.catalogue, time, windowMs: () => windowMs });
        if (said.assertive !== undefined) write(deps.assertiveRegion(), said.assertive);
        if (said.polite !== undefined) deps.polite(said.polite);
      }
      lastState = state;
      emit();
    });
    emit();
  }

  function startPolling(seed?: RoomState, anonymous = false): void {
    mode = 'polling';
    const api = anonymous ? { ...deps.api, live: (id: string) => deps.api.live(id, true) } : deps.api;
    const next = createRoomController({
      auctionId: deps.auctionId,
      api,
      socket: null,
      timers,
      hidden: () => document.hidden,
      ...(seed === undefined ? {} : { initial: seed }),
    });
    attach(next);
    void next.start();
  }

  function startSocket(): void {
    mode = 'socket';
    const ws = createSocket({
      url: `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/api/v1/ws`,
      open: openBrowserSocket,
      getToken: deps.auth.getToken,
      refresh: deps.auth.refresh,
      now: () => Date.now(),
      timers,
      random: Math.random,
      online: () => navigator.onLine,
      onFrame: (frame) => {
        deps.clock.observe({ serverTimeMs: frame.server_time_ms, receivedAtMs: Date.now() });
        if (frame.type === 'notification') deps.onNotification?.();
        controller?.handleFrame(frame);
      },
      onStatus: (status) => {
        if (status.closed !== null) {
          // 4004, 4012, 4009, 4010: no reconnect; this tab polls from here (item 2). On 4004 the
          // session is gone on the server: forget the token here (no refresh), so the shell signs out
          if (status.closed.code === 4004) deps.auth.forget();
          switchToPolling(status.closed.code);
          return;
        }
        controller?.setConnection(status.state);
      },
      onResync: async () => {
        await controller?.resync();
      },
    });
    socket = ws;
    const next = createRoomController({ auctionId: deps.auctionId, api: deps.api, socket: ws, timers, hidden: () => document.hidden });
    attach(next);
    void next.start();
    void ws.connect();
  }

  function stopCurrent(): void {
    controller?.stop();
    socket?.close();
    socket = null;
  }

  function switchToPolling(closedCode: number | null): void {
    if (mode === 'polling' || stopped) return;
    // keep what the room showed; a revoked token (4004) or a refused one (4002) polls the public view
    const last = controller?.getState();
    stopCurrent();
    const seed = last === undefined ? undefined : { ...last, connection: 'polling' as const, subscribed: false, buffer: [], gapPending: false, pending: null };
    startPolling(seed, closedCode === 4004 || closedCode === 4002);
    controller?.setConnection('polling', closedCode);
  }

  const onOnline = () => socket?.setOnline(true);
  const onOffline = () => socket?.setOnline(false);

  return {
    subscribe(listen: () => void): () => void {
      listeners.add(listen);
      return () => listeners.delete(listen);
    },
    getState: (): RoomState => controller?.getState() ?? empty,
    getMode: () => mode,
    ownsSocket: () => tabs?.owner ?? false,
    getLastUpdateMs: () => lastUpdateMs,
    setWindowMs: (ms: number) => {
      windowMs = ms;
    },
    placeBid: (amount: number) => controller?.placeBid(amount) ?? null,
    setProxy: (amount: number) => controller?.setProxy(amount) ?? null,
    countdownZero: () => controller?.dispatch({ type: 'countdownZero' }),
    setSeller: (isSeller: boolean) => controller?.dispatch({ type: 'seller', isSeller }),
    announcePolite: (text: string) => deps.polite(text),
    /** "Use this tab" (item 3): take the socket from the tab that holds it */
    useThisTab: () => void tabs?.useThisTab(),

    /** Poll at once; a signed-in viewer also claims the socket lock (anonymous: polling only, FR-28). */
    start(): void {
      stopped = false;
      startPolling();
      if (deps.auth.getToken() !== null) {
        tabs = createTabs({
          request: browserLockRequest(),
          onOwner: (owner) => {
            if (stopped) return;
            if (owner && mode !== 'socket') {
              stopCurrent();
              startSocket();
            } else if (!owner && mode === 'socket') {
              switchToPolling(4012);
            }
          },
        });
        void tabs.claim();
      }
      window.addEventListener('online', onOnline);
      window.addEventListener('offline', onOffline);
    },

    stop(): void {
      stopped = true;
      window.removeEventListener('online', onOnline);
      window.removeEventListener('offline', onOffline);
      unsubscribe?.();
      unsubscribe = null;
      stopCurrent();
      tabs?.releaseLock();
      tabs = null;
      mode = 'polling';
    },
  };
}
