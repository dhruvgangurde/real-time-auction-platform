// Server time (brief 13 item 12, UI-4, FR-31, OR-1). Countdowns come only from end_time_ms and
// the server clock: the offset is estimated from every server_time_ms seen, keeping the estimate
// from the fastest round trip, and a local timer only repaints.

export type Sample = {
  serverTimeMs: number;
  /** local time the response or frame arrived */
  receivedAtMs: number;
  /** local time the request was sent; absent for a pushed frame */
  sentAtMs?: number;
};

export class ServerClock {
  private offsetMs: number | null = null;
  private bestRttMs = Number.POSITIVE_INFINITY;

  constructor(private readonly localNow: () => number = () => Date.now()) {}

  /** Offset = server time minus local time; null until the first sample. */
  get offset(): number | null {
    return this.offsetMs;
  }

  observe(sample: Sample): void {
    if (sample.sentAtMs !== undefined) {
      // a round trip: the server stamped the body about halfway through it
      const rtt = Math.max(0, sample.receivedAtMs - sample.sentAtMs);
      if (rtt <= this.bestRttMs) {
        this.bestRttMs = rtt;
        this.offsetMs = sample.serverTimeMs - (sample.sentAtMs + rtt / 2);
      }
      return;
    }
    // a pushed frame was stamped before it arrived, so this is a lower bound on the offset:
    // it can raise a too-low estimate but never lowers a round-trip one
    const lowerBound = sample.serverTimeMs - sample.receivedAtMs;
    if (this.offsetMs === null || lowerBound > this.offsetMs) {
      this.offsetMs = lowerBound;
    }
  }

  /** The server's clock now, or local time before any sample. */
  now(): number {
    return this.localNow() + (this.offsetMs ?? 0);
  }

  /** Milliseconds until end_time_ms, never below zero. */
  remainingMs(endTimeMs: number): number {
    return Math.max(0, endTimeMs - this.now());
  }
}

/** A UTC instant shown in the viewer's zone (OR-1); timeZone is injectable for tests. */
export function formatLocal(
  ms: number,
  options: { timeZone?: string; seconds?: boolean } = {},
): string {
  const formatter = new Intl.DateTimeFormat('en-IN', {
    hour: '2-digit',
    minute: '2-digit',
    ...(options.seconds ? { second: '2-digit' } : {}),
    hourCycle: 'h23',
    ...(options.timeZone ? { timeZone: options.timeZone } : {}),
  });
  return formatter.format(new Date(ms));
}

/** A datetime-local input value (the viewer's zone) as a UTC ISO string for the API. */
export function toUtcIso(localInput: string): string {
  const date = new Date(localInput);
  if (Number.isNaN(date.getTime())) {
    throw new RangeError('not a datetime-local value');
  }
  return date.toISOString();
}
