// The app's one polite live region queue (brief 11.4): at most one polite announcement every
// 3 s, and a text arriving inside the gap replaces the pending one, so the latest is said. The
// room (prices, extensions, milestones) and the shell (new notifications) share it, so neither
// cuts the other off.
export const POLITE_GAP_MS = 3_000;

/** At most one polite announcement per 3 s; a text arriving inside the gap replaces the pending one. */
export class PoliteQueue {
  private lastAt = Number.NEGATIVE_INFINITY;
  private pending: string | null = null;
  private timer: unknown = null;

  constructor(
    private readonly emit: (text: string) => void,
    private readonly now: () => number,
    private readonly timers: { setTimeout: (fn: () => void, ms: number) => unknown; clearTimeout: (h: unknown) => void },
  ) {}

  offer(text: string): void {
    const wait = this.lastAt + POLITE_GAP_MS - this.now();
    if (wait <= 0 && this.pending === null) {
      this.send(text);
      return;
    }
    this.pending = text; // the latest wins
    if (this.timer === null) {
      this.timer = this.timers.setTimeout(() => {
        this.timer = null;
        const next = this.pending;
        this.pending = null;
        if (next !== null) this.send(next);
      }, Math.max(0, wait));
    }
  }

  private send(text: string): void {
    this.lastAt = this.now();
    this.emit(text);
  }
}

let shared: PoliteQueue | null = null;

/** Say `text` in the shell's polite region (#live-polite) through the shared queue. */
export function announcePolite(text: string): void {
  shared ??= new PoliteQueue(
    (said) => {
      const region = typeof document === 'undefined' ? null : document.getElementById('live-polite');
      if (region === null) return;
      region.textContent = ''; // a repeat is announced again
      region.textContent = said;
    },
    () => Date.now(),
    { setTimeout: (fn, ms) => setTimeout(fn, ms), clearTimeout: (h) => clearTimeout(h as ReturnType<typeof setTimeout>) },
  );
  shared.offer(text);
}
