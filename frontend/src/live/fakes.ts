// Test doubles shared by the live tests: a fake WebSocket, fake timers and a fake lock manager.
// Test code only; nothing here ships.
import type { LockRequest } from './tabs';
import type { Timers, WsLike } from './socket';

export class FakeWs implements WsLike {
  readyState = 0;
  sent: string[] = [];
  closedWith: { code: number | undefined; reason: string | undefined } | null = null;
  onopen: ((ev: unknown) => void) | null = null;
  onmessage: ((ev: { data: unknown }) => void) | null = null;
  onclose: ((ev: { code: number; reason: string }) => void) | null = null;
  onerror: ((ev: unknown) => void) | null = null;

  constructor(readonly url: string) {}

  send(data: string): void {
    if (this.readyState !== 1) throw new Error('send on a socket that is not open');
    this.sent.push(data);
  }

  close(code?: number, reason?: string): void {
    this.readyState = 3;
    this.closedWith = { code, reason };
  }

  /** the server accepted the connection */
  open(): void {
    this.readyState = 1;
    this.onopen?.({});
  }

  /** a frame from the server */
  receive(frame: unknown): void {
    this.onmessage?.({ data: typeof frame === 'string' ? frame : JSON.stringify(frame) });
  }

  /** the server (or the network) closed the connection */
  serverClose(code: number, reason = ''): void {
    this.readyState = 3;
    this.onclose?.({ code, reason });
  }

  get frames(): Record<string, unknown>[] {
    return this.sent.map((s) => JSON.parse(s) as Record<string, unknown>);
  }
}

export class FakeTimers implements Timers {
  private time = 0;
  private seq = 0;
  private pending: { at: number; seq: number; fn: () => void }[] = [];

  now = (): number => this.time;

  setTimeout = (fn: () => void, ms: number): unknown => {
    const handle = { at: this.time + ms, seq: this.seq++, fn };
    this.pending.push(handle);
    return handle;
  };

  clearTimeout = (handle: unknown): void => {
    this.pending = this.pending.filter((p) => p !== handle);
  };

  /** advance the clock, running every timer that comes due, in order */
  async advance(ms: number): Promise<void> {
    const until = this.time + ms;
    for (;;) {
      const due = this.pending.filter((p) => p.at <= until).sort((a, b) => a.at - b.at || a.seq - b.seq)[0];
      if (due === undefined) break;
      this.pending = this.pending.filter((p) => p !== due);
      this.time = due.at;
      due.fn();
      await Promise.resolve();
      await Promise.resolve();
    }
    this.time = until;
  }
}

/** A fake navigator.locks: exclusive, queued, with steal and abort. */
export class FakeLocks {
  private holder: { release: () => void; reject: (e: Error) => void } | null = null;
  private queue: { grant: () => void; signal: AbortSignal | undefined }[] = [];

  request: LockRequest = (name, options, callback) => {
    if (name !== 'auction-socket') throw new Error('unexpected lock ' + name);
    return new Promise((resolve, reject) => {
      const grant = () => {
        const done = callback(true);
        const holder = {
          release: () => {
            this.holder = null;
            this.next();
          },
          reject: (e: Error) => {
            this.holder = null;
            reject(e);
          },
        };
        this.holder = holder;
        void done.then(
          (value) => {
            if (this.holder === holder) holder.release();
            resolve(value);
          },
          (e) => {
            if (this.holder === holder) holder.release();
            reject(e as Error);
          },
        );
      };
      if (options.steal && this.holder !== null) {
        this.holder.reject(new DOMException('stolen', 'AbortError'));
        grant();
        return;
      }
      if (this.holder === null) {
        grant();
        return;
      }
      const entry = { grant, signal: options.signal };
      this.queue.push(entry);
      options.signal?.addEventListener('abort', () => {
        this.queue = this.queue.filter((q) => q !== entry);
        reject(new DOMException('aborted', 'AbortError'));
      });
    });
  };

  private next(): void {
    const entry = this.queue.shift();
    if (entry !== undefined) entry.grant();
  }
}

export const tick = (): Promise<void> => new Promise((r) => setTimeout(r, 0));
