// Session state (brief 13 items 3 and 22, NFR-17, NFR-21). The access token lives only in this
// module's memory: never in localStorage, sessionStorage, IndexedDB or a URL. On load the app
// asks the other tabs of this browser for their token over BroadcastChannel; failing that it
// refreshes under a Web Lock, so two tabs never refresh at once (the second would fail after the
// rotation), and the new token is shared to the other tabs in memory. A refresh with no cookie
// answers 204: the viewer is anonymous and nothing is logged.
import { BASE } from './client';
import { parseTokenResponse } from '../lib/contract';

export type Channel = {
  postMessage: (message: unknown) => void;
  onmessage: ((event: { data: unknown }) => void) | null;
  close: () => void;
};

export type Locks = {
  request: <T>(name: string, callback: () => Promise<T>) => Promise<T>;
};

export type AuthDeps = {
  fetch: typeof fetch;
  now: () => number;
  sleep: (ms: number) => Promise<void>;
  /** the browser's BroadcastChannel for this origin, or null where unavailable */
  channel: () => Channel | null;
  /** navigator.locks, or null where unavailable (then refresh runs unguarded) */
  locks: Locks | null;
  /** how long to wait for a peer tab's token before refreshing */
  peerWaitMs?: number;
};

type Message =
  | { kind: 'who-has-token' }
  | { kind: 'token'; token: string; expiresAtMs: number }
  | { kind: 'logout' };

export const CHANNEL = 'auction-auth';
export const LOCK = 'auction-refresh';
export const REQUESTED_WITH = 'fetch';

export type Auth = ReturnType<typeof createAuth>;

export function createAuth(deps: AuthDeps) {
  let token: string | null = null;
  let expiresAtMs = 0;
  let restored = false; // restoreOnLoad has settled: guards may decide (UI-2)
  const listeners = new Set<(token: string | null) => void>();
  const channel = deps.channel();
  let waitingForPeer: ((token: string) => void) | null = null;
  // bumped by every login (accept), logout and forget: a load-time restore applies its result only
  // if none of them happened since it started, so a late restore never writes or clears the token
  let epoch = 0;
  // the in-flight load-time restore, one promise for everyone (login waits for it)
  let ready: Promise<string | null> = Promise.resolve(null);
  // the epoch at which the load-time restore could not reach the server (network, 5xx): until a
  // login, logout or forget moves the epoch, a guard shows "try again" instead of signing out
  let unreachableAt: number | null = null;

  function set(next: string | null, expires = 0): void {
    token = next;
    expiresAtMs = expires;
    for (const listen of listeners) listen(token);
  }

  function post(message: Message): void {
    channel?.postMessage(message);
  }

  if (channel !== null) {
    channel.onmessage = (event) => {
      const m = event.data as Message;
      if (typeof m !== 'object' || m === null) return;
      if (m.kind === 'who-has-token' && token !== null) {
        post({ kind: 'token', token, expiresAtMs });
      } else if (m.kind === 'token' && typeof m.token === 'string' && m.expiresAtMs > expiresAtMs) {
        set(m.token, m.expiresAtMs); // adopt a newer token from another tab, in memory
        waitingForPeer?.(m.token);
      } else if (m.kind === 'logout') {
        set(null);
      }
    };
  }

  /** What the last refresh learned: a token; anonymous (204); the session is dead (401); or
   * the server could not say (network, 5xx, a malformed body), in which case the token in
   * memory is kept. Only anonymous and dead mean there is no session on the server. */
  type RefreshOutcome = 'token' | 'anonymous' | 'dead' | 'unavailable';
  let lastRefresh: RefreshOutcome = 'unavailable';

  /** POST /auth/refresh: 200 a new token, 204 anonymous, 401 the session is gone. With
   * `startedAt` (the restore's epoch), the result is applied only if the epoch has not moved. */
  async function refresh(startedAt?: number): Promise<string | null> {
    let response: Response;
    try {
      response = await deps.fetch(`${BASE}/auth/refresh`, {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'X-Requested-With': REQUESTED_WITH, Accept: 'application/json' },
      });
    } catch {
      lastRefresh = 'unavailable';
      return token; // offline: keep what we have
    }
    if (startedAt !== undefined && startedAt !== epoch) return token; // a login, logout or forget won
    if (response.status === 204) {
      lastRefresh = 'anonymous';
      set(null);
      return null;
    }
    if (response.status === 401) {
      lastRefresh = 'dead'; // the refresh cookie is invalid, expired or revoked
      set(null);
      return null;
    }
    const parsed = response.ok ? parseTokenResponse(await response.json().catch(() => undefined)) : null;
    if (startedAt !== undefined && startedAt !== epoch) return token; // moved while the body was read
    if (parsed === null) {
      lastRefresh = 'unavailable'; // a 5xx or a body this client does not understand
      return token;
    }
    lastRefresh = 'token';
    set(parsed.access_token, deps.now() + parsed.expires_in * 1000);
    post({ kind: 'token', token: parsed.access_token, expiresAtMs });
    return parsed.access_token;
  }

  async function refreshUnderLock(startedAt?: number): Promise<string | null> {
    const before = token;
    const run = async () => {
      // a peer may have refreshed and shared its token while we waited for the lock
      if (token !== null && token !== before) return token;
      return refresh(startedAt);
    };
    return deps.locks === null ? run() : deps.locks.request(LOCK, run);
  }

  async function restore(startedAt: number): Promise<string | null> {
    try {
      if (channel !== null) {
        const fromPeer = new Promise<string | null>((resolve) => {
          waitingForPeer = resolve;
          post({ kind: 'who-has-token' });
          void deps.sleep(deps.peerWaitMs ?? 150).then(() => resolve(null));
        });
        const peer = await fromPeer;
        waitingForPeer = null;
        if (peer !== null) return peer;
      }
      return await refreshUnderLock(startedAt);
    } finally {
      restored = true;
      unreachableAt = startedAt === epoch && token === null && lastRefresh === 'unavailable' ? epoch : null;
      for (const listen of listeners) listen(token);
    }
  }

  return {
    getToken: () => token,
    expiresAt: () => expiresAtMs,
    refresh: () => refreshUnderLock(),

    /** The in-flight load-time restore (resolved when none was started). */
    get ready(): Promise<string | null> {
      return ready;
    },

    /** On every load: a peer tab's token if one answers, otherwise a refresh under the lock. Its
     * result is dropped if a login, logout or forget happened meanwhile (the epoch moved). */
    restoreOnLoad(): Promise<string | null> {
      const startedAt = epoch;
      ready = restore(startedAt);
      return ready;
    },

    /** Whether the load-time restore has settled, so a guard can tell anonymous from pending. */
    isRestored: () => restored,

    /** The restore could not reach the server (network error, 5xx): there may still be a session,
     * so a guard offers a retry (restoreOnLoad again) instead of a sign-in redirect. */
    isUnreachable: () => unreachableAt === epoch && token === null,

    /** After login: keep the token in memory and share it with the other tabs. */
    accept(accessToken: string, expiresInS: number): void {
      epoch += 1;
      set(accessToken, deps.now() + expiresInS * 1000);
      post({ kind: 'token', token: accessToken, expiresAtMs });
    },

    /** Revoke the session on the server, then forget the token everywhere. Returns whether the
     * revocation went through: 204 from the server, or a refresh that found no live session.
     * A 401 only says the access token was refused (it may have expired while the refresh
     * session is still alive), so it refreshes and revokes again with the new token. On false
     * the refresh cookie may still be alive and the UI must say the sign-out did not go through. */
    async logout(): Promise<boolean> {
      epoch += 1;
      const revoke = async (bearer: string): Promise<number> => {
        try {
          const response = await deps.fetch(`${BASE}/auth/logout`, {
            method: 'POST',
            credentials: 'same-origin',
            headers: { Authorization: `Bearer ${bearer}` },
          });
          return response.status;
        } catch {
          return 0; // network: unknown, so not revoked
        }
      };
      let revoked = false;
      const current = token;
      let status = current === null ? 401 : await revoke(current);
      if (status === 401) {
        // the bearer was refused or absent: ask the server for a live session, under the lock
        // like every refresh (two tabs must never rotate the cookie at once)
        const fresh = await refreshUnderLock();
        if (fresh !== null && fresh !== current) {
          status = await revoke(fresh);
        } else if (fresh === null && (lastRefresh === 'anonymous' || lastRefresh === 'dead')) {
          revoked = true; // the server has no session for this browser: nothing to revoke
        }
        // otherwise the server could not say (unavailable) or gave back the same refused
        // token: not revoked
      }
      revoked = revoked || status === 204;
      set(null);
      post({ kind: 'logout' });
      return revoked;
    },

    /** The server revoked this session (socket close 4004): drop the token in this tab only,
     * with no logout call and no refresh; the shell then shows the signed-out header. */
    forget(): void {
      epoch += 1;
      set(null);
    },

    onChange(listen: (token: string | null) => void): () => void {
      listeners.add(listen);
      return () => listeners.delete(listen);
    },

    close(): void {
      channel?.close();
    },
  };
}

/** The browser's channel and locks, or null where the API is missing. */
export function browserAuthDeps(): Pick<AuthDeps, 'channel' | 'locks'> {
  return {
    channel: () => {
      if (typeof BroadcastChannel !== 'function') return null;
      const bc = new BroadcastChannel(CHANNEL);
      const adapter: Channel = {
        onmessage: null,
        postMessage: (message) => bc.postMessage(message),
        close: () => bc.close(),
      };
      bc.onmessage = (event) => adapter.onmessage?.({ data: event.data as unknown });
      return adapter;
    },
    locks:
      typeof navigator !== 'undefined' && navigator.locks
        ? { request: (name, callback) => navigator.locks.request(name, callback) }
        : null,
  };
}
