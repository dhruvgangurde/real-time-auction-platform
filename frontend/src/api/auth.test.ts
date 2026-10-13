// brief 13 items 3 and 22: the token lives in memory only; refresh on load with X-Requested-With;
// one refresh across tabs under a lock; the token shared over the channel, never stored.
import { afterEach, beforeEach, describe, expect, test } from 'vitest';

import { createAuth, type AuthDeps, type Channel, type Locks } from './auth';

/** A fake BroadcastChannel bus: a message goes to every other channel, asynchronously. */
class Bus {
  readonly channels = new Set<Channel>();
  open(): Channel {
    const self: Channel = {
      onmessage: null,
      postMessage: (message) => {
        for (const other of this.channels) {
          if (other !== self) queueMicrotask(() => other.onmessage?.({ data: message }));
        }
      },
      close: () => this.channels.delete(self),
    };
    this.channels.add(self);
    return self;
  }
}

/** A fake Web Lock: callbacks for one name run one after another. */
function fakeLocks(): Locks & { held: number } {
  const queues = new Map<string, Promise<unknown>>();
  const locks = {
    held: 0,
    request: async <T>(name: string, callback: () => Promise<T>): Promise<T> => {
      const previous = queues.get(name) ?? Promise.resolve();
      const run = previous.then(async () => {
        locks.held += 1;
        try {
          return await callback();
        } finally {
          locks.held -= 1;
        }
      });
      queues.set(name, run.catch(() => undefined));
      return run;
    },
  };
  return locks;
}

type Call = { url: string; init: RequestInit };

function tab(bus: Bus, locks: Locks | null, answers: (() => Response)[], calls: Call[]) {
  const fetchImpl: typeof globalThis.fetch = async (url, init) => {
    calls.push({ url: String(url), init: init ?? {} });
    const next = answers.shift();
    if (next === undefined) throw new Error('unexpected request');
    return next();
  };
  const deps: AuthDeps = {
    fetch: fetchImpl,
    now: () => 1_000_000,
    sleep: () => new Promise((r) => setTimeout(r, 5)),
    channel: () => bus.open(),
    locks,
    peerWaitMs: 1,
  };
  return createAuth(deps);
}

const ok = (token: string) => () =>
  new Response(JSON.stringify({ access_token: token, token_type: 'bearer', expires_in: 900 }), { status: 200 });
const anonymous = () => new Response(null, { status: 204 });
const dead = () =>
  new Response(JSON.stringify({ error: { code: 'INVALID_TOKEN', message: 'bad' } }), { status: 401 });

const STORAGE = ['localStorage', 'sessionStorage', 'indexedDB'] as const;

beforeEach(() => {
  // any touch of browser storage fails the test (item 22)
  for (const name of STORAGE) {
    Object.defineProperty(globalThis, name, {
      configurable: true,
      get() {
        throw new Error(`${name} must never be touched`);
      },
    });
  }
});

afterEach(() => {
  for (const name of STORAGE) delete (globalThis as Record<string, unknown>)[name];
});

describe('restore on load (item 22)', () => {
  test('sends X-Requested-With and same-origin credentials, keeps the token in memory', async () => {
    const calls: Call[] = [];
    const auth = tab(new Bus(), fakeLocks(), [ok('t-1')], calls);
    expect(await auth.restoreOnLoad()).toBe('t-1');
    expect(calls[0]?.url).toBe('/api/v1/auth/refresh');
    expect(calls[0]?.init).toMatchObject({ method: 'POST', credentials: 'same-origin' });
    expect(calls[0]?.init.headers).toMatchObject({ 'X-Requested-With': 'fetch' });
    expect(auth.getToken()).toBe('t-1');
    expect(auth.expiresAt()).toBe(1_000_000 + 900_000);
  });

  test('204 leaves the viewer anonymous', async () => {
    const auth = tab(new Bus(), fakeLocks(), [anonymous], []);
    expect(await auth.restoreOnLoad()).toBeNull();
    expect(auth.getToken()).toBeNull();
  });

  test('401 clears the token; a malformed body is anonymous', async () => {
    const auth = tab(new Bus(), fakeLocks(), [ok('t-1'), dead, () => new Response('{}', { status: 200 })], []);
    await auth.restoreOnLoad();
    expect(await auth.refresh()).toBeNull();
    expect(auth.getToken()).toBeNull();
    expect(await auth.refresh()).toBeNull();
  });

  test('works without the Locks API', async () => {
    const auth = tab(new Bus(), null, [ok('t-1')], []);
    expect(await auth.restoreOnLoad()).toBe('t-1');
  });
});

describe('tabs (item 3)', () => {
  test('two tabs restoring at once make one refresh call', async () => {
    const bus = new Bus();
    const locks = fakeLocks();
    const callsA: Call[] = [];
    const callsB: Call[] = [];
    const a = tab(bus, locks, [ok('t-1')], callsA);
    const b = tab(bus, locks, [], callsB);
    const [ta, tb] = await Promise.all([a.restoreOnLoad(), b.restoreOnLoad()]);
    expect([ta, tb]).toEqual(['t-1', 't-1']);
    expect(callsA).toHaveLength(1);
    expect(callsB).toHaveLength(0); // inside the lock it found the token A had shared
  });

  test('a later tab gets the token from a peer and never calls refresh', async () => {
    const bus = new Bus();
    const locks = fakeLocks();
    const first = tab(bus, locks, [ok('t-1')], []);
    await first.restoreOnLoad();
    const callsLater: Call[] = [];
    const later = tab(bus, locks, [], callsLater);
    expect(await later.restoreOnLoad()).toBe('t-1');
    expect(callsLater).toHaveLength(0);
  });

  test('a login is shared to the other tabs and a logout clears them all', async () => {
    const bus = new Bus();
    const a = tab(bus, fakeLocks(), [() => new Response(null, { status: 204 })], []);
    const b = tab(bus, fakeLocks(), [], []);
    a.accept('t-login', 900);
    await new Promise((r) => setTimeout(r, 5));
    expect(b.getToken()).toBe('t-login');
    expect(await a.logout()).toBe(true);
    await new Promise((r) => setTimeout(r, 5));
    expect(a.getToken()).toBeNull();
    expect(b.getToken()).toBeNull();
  });

  test('a logout the server did not confirm reports false and still forgets the token', async () => {
    const calls: Call[] = [];
    const a = tab(new Bus(), fakeLocks(), [() => new Response(null, { status: 503 })], calls);
    a.accept('t-login', 900);
    expect(await a.logout()).toBe(false);
    expect(calls[0]?.url).toBe('/api/v1/auth/logout');
    expect(calls[0]?.init.headers).toMatchObject({ Authorization: 'Bearer t-login' });
    expect(a.getToken()).toBeNull();
    // anonymous: nothing to revoke, nothing sent
    expect(await a.logout()).toBe(true);
    expect(calls).toHaveLength(1);
  });

  test('a newer token replaces an older one; an older one is ignored', async () => {
    const bus = new Bus();
    const a = tab(bus, fakeLocks(), [], []);
    const b = tab(bus, fakeLocks(), [], []);
    a.accept('t-new', 900);
    await new Promise((r) => setTimeout(r, 5));
    const self = bus.open();
    self.postMessage({ kind: 'token', token: 't-old', expiresAtMs: 1 });
    await new Promise((r) => setTimeout(r, 5));
    expect(b.getToken()).toBe('t-new');
  });
});
