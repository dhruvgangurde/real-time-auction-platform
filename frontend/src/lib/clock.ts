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

const pad = (n: number, width = 2) => String(n).padStart(width, '0');

/** "4:05", "0:09", "1:02:30": a span rounded up to whole seconds, for countdowns and extensions. */
export function formatRemaining(ms: number): string {
  const s = Math.max(0, Math.ceil(ms / 1000));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return h > 0 ? `${h}:${pad(m)}:${pad(s % 60)}` : `${m}:${pad(s % 60)}`;
}

export type SpanUnits = { minutes: (n: number) => string; seconds: (n: number) => string; minute: string };

/** "2 minutes", "1 minute 30 seconds", "20 seconds" in the catalogue's words. */
export function formatSpan(ms: number, units: SpanUnits): string {
  const s = Math.round(ms / 1000);
  const m = Math.floor(s / 60);
  const rest = s % 60;
  const parts: string[] = [];
  if (m > 0) parts.push(units.minutes(m));
  if (rest > 0 || m === 0) parts.push(units.seconds(rest));
  return parts.join(' ');
}

/** The soft-close window as a phrase after "the last": "minute" for 60 s, else the span. */
export function formatWindow(ms: number, units: SpanUnits): string {
  return ms === 60_000 ? units.minute : formatSpan(ms, units);
}

/** "20:01:12.406": a decision time to the millisecond, in the viewer's zone (ledger rows). */
export function formatDecided(ms: number, timeZone?: string): string {
  return `${formatLocal(ms, timeZone ? { timeZone, seconds: true } : { seconds: true })}.${pad(ms % 1000, 3)}`;
}

/** "14 Oct": the day of an instant in the viewer's zone. */
export function formatDay(ms: number, timeZone?: string): string {
  return new Intl.DateTimeFormat('en-IN', { day: 'numeric', month: 'short', ...(timeZone ? { timeZone } : {}) }).format(new Date(ms));
}
