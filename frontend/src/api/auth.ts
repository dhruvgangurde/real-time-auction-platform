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
  const listeners = new Set<(token: string | null) => void>();
  const channel = deps.channel();
  let waitingForPeer: ((token: string) => void) | null = null;

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

  /** POST /auth/refresh: 200 a new token, 204 anonymous, 401 the session is gone. */
  async function refresh(): Promise<string | null> {
    let response: Response;
    try {
      response = await deps.fetch(`${BASE}/auth/refresh`, {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'X-Requested-With': REQUESTED_WITH, Accept: 'application/json' },
      });
    } catch {
      return token; // offline: keep what we have
    }
    if (response.status === 204) {
      set(null);
      return null;
    }
    if (!response.ok) {
      set(null); // 401: the refresh cookie is invalid, expired or revoked
      return null;
    }
    const parsed = parseTokenResponse(await response.json().catch(() => undefined));
    if (parsed === null) {
      set(null);
      return null;
    }
    set(parsed.access_token, deps.now() + parsed.expires_in * 1000);
    post({ kind: 'token', token: parsed.access_token, expiresAtMs });
    return parsed.access_token;
  }

  async function refreshUnderLock(): Promise<string | null> {
    const before = token;
    const run = async () => {
      // a peer may have refreshed and shared its token while we waited for the lock
      if (token !== null && token !== before) return token;
      return refresh();
    };
    return deps.locks === null ? run() : deps.locks.request(LOCK, run);
  }

  return {
    getToken: () => token,
    expiresAt: () => expiresAtMs,
    refresh: refreshUnderLock,

    /** On every load: a peer tab's token if one answers, otherwise a refresh under the lock. */
    async restoreOnLoad(): Promise<string | null> {
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
      return refreshUnderLock();
    },

    /** After login: keep the token in memory and share it with the other tabs. */
    accept(accessToken: string, expiresInS: number): void {
      set(accessToken, deps.now() + expiresInS * 1000);
      post({ kind: 'token', token: accessToken, expiresAtMs });
    },

    /** Revoke the session on the server, then forget the token everywhere. Returns whether the
     * server confirmed the revocation (204, or 401 for a session already dead); on false the
     * refresh cookie may still be alive and the UI must say the sign-out did not go through. */
    async logout(): Promise<boolean> {
      const current = token;
      let revoked = current === null; // nothing to revoke
      if (current !== null) {
        try {
          const response = await deps.fetch(`${BASE}/auth/logout`, {
            method: 'POST',
            credentials: 'same-origin',
            headers: { Authorization: `Bearer ${current}` },
          });
          revoked = response.status === 204 || response.status === 401;
        } catch {
          revoked = false;
        }
      }
      set(null);
      post({ kind: 'logout' });
      return revoked;
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
