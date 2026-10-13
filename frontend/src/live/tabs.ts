// One socket per session across the tabs of a browser (brief 13 item 3, CI-3). The tab holding
// the Web Lock "auction-socket" owns the socket; every other tab waits in the lock's queue (so
// it takes over when the owner closes) and meanwhile polls /live every 2 s and bids over REST.
// "Use this tab" steals the lock; the server then closes the old tab's socket with 4012, which
// never reconnects (socket.ts), and the old tab falls back to polling.

export type LockRequest = <T>(
  name: string,
  options: { steal?: boolean; signal?: AbortSignal },
  callback: (granted: boolean) => Promise<T>,
) => Promise<T>;

export type TabsDeps = {
  /** navigator.locks.request, or null where unavailable (then this tab always owns the socket) */
  request: LockRequest | null;
  onOwner: (owner: boolean) => void;
};

export const SOCKET_LOCK = 'auction-socket';

export type Tabs = ReturnType<typeof createTabs>;

export function createTabs(deps: TabsDeps) {
  let owner = false;
  let release: (() => void) | null = null;
  let waiting: AbortController | null = null;

  function set(next: boolean): void {
    if (next === owner) return;
    owner = next;
    deps.onOwner(owner);
  }

  /** Hold the lock until released; a steal by another tab rejects the request. */
  function hold(options: { steal?: boolean }): Promise<void> {
    if (deps.request === null) {
      set(true);
      return Promise.resolve();
    }
    waiting?.abort();
    const controller = new AbortController();
    waiting = controller;
    let granted = false; // only a request that held the lock can take ownership away
    const held = deps.request(
      SOCKET_LOCK,
      { ...options, signal: controller.signal },
      (ok) =>
        new Promise<void>((resolve) => {
          if (!ok) {
            resolve();
            return;
          }
          granted = true;
          waiting = null;
          release = resolve;
          set(true);
        }),
    );
    const ended = () => {
      // released by us, stolen by another tab, or aborted while queued
      if (granted) {
        release = null;
        set(false);
      }
    };
    return held.then(ended, ended);
  }

  return {
    get owner(): boolean {
      return owner;
    },

    /** On load: own the socket if free, otherwise wait for it (polling meanwhile). */
    claim(): Promise<void> {
      return hold({});
    },

    /** "Use this tab": take the socket from the tab that holds it. */
    useThisTab(): Promise<void> {
      return hold({ steal: true });
    },

    /** Give the socket up (leaving the page); the next waiting tab takes over. */
    releaseLock(): void {
      waiting?.abort();
      waiting = null;
      const r = release;
      release = null;
      set(false);
      r?.();
    },
  };
}

/** navigator.locks as a LockRequest, or null where the API is missing. */
export function browserLockRequest(): LockRequest | null {
  if (typeof navigator === 'undefined' || !navigator.locks) return null;
  return (name, options, callback) =>
    navigator.locks.request(name, { mode: 'exclusive', ...options }, (lock) => callback(lock !== null));
}
