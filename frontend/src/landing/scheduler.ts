// The landing's one anonymous request queue (slice 5 amendment 1). Every REST request the landing
// makes goes through it: GET /demo, the ledger pages of the replay, the stress run's status polls,
// the featured lots. At most RATE requests start in any WINDOW_MS, one below the 5 per second
// per-address anonymous limit (brief 12.2; GET /demo and the run status share one bucket, the bid
// history another), so the page alone never meets a 429. If one comes anyway (another tab, another
// browser on the same address), its Retry-After pauses every start, and the refused request is
// retried first. High priority (what the viewer is watching) goes before low (pages still loading).
import { ApiError } from '../api/client';

export const RATE = 4;
export const WINDOW_MS = 1000;
const RETRIES = 3; // after the first refusal; then the 429 is the caller's
const DEFAULT_PAUSE_MS = 1000; // a 429 without Retry-After
// a read limit's window is a second; a longer Retry-After is another limit (a start refused for
// the hour) and belongs to the caller, without pausing the page
const MAX_PAUSE_MS = 5000;

export type Priority = 'high' | 'low';

export type SchedulerDeps = {
  now: () => number;
  setTimer: (fn: () => void, ms: number) => unknown;
};

type Job = {
  task: () => Promise<unknown>;
  priority: Priority;
  cancelled: () => boolean;
  refusals: number;
  resolve: (value: unknown) => void;
  reject: (error: unknown) => void;
};

export type Scheduler = {
  /** Runs `task` when the rate allows; `cancelled` is asked just before it would start. */
  run<T>(task: () => Promise<T>, priority?: Priority, cancelled?: () => boolean): Promise<T>;
  /** The landing unmounted: queued requests are dropped (never sent), new ones refused. */
  close(): void;
  open(): void;
};

const cancelledError = () => new ApiError('CANCELLED', 'no longer needed', 0);

export function createScheduler(deps: SchedulerDeps): Scheduler {
  const queue: Job[] = [];
  const starts: number[] = [];
  let pausedUntil = 0;
  let armed = false;
  let closed = false;

  function next(): Job | undefined {
    const index = queue.findIndex((j) => j.priority === 'high');
    return queue.splice(index === -1 ? 0 : index, 1)[0];
  }

  function pump(): void {
    const now = deps.now();
    while (starts.length > 0 && starts[0]! <= now - WINDOW_MS) starts.shift();
    while (queue.length > 0 && now >= pausedUntil && starts.length < RATE) {
      const job = next()!;
      if (job.cancelled()) {
        job.reject(cancelledError());
        continue;
      }
      starts.push(now);
      void start(job);
    }
    if (queue.length > 0 && !armed) {
      const freeAt = starts.length >= RATE ? starts[0]! + WINDOW_MS : now;
      armed = true;
      deps.setTimer(() => {
        armed = false;
        pump();
      }, Math.max(pausedUntil, freeAt) - now);
    }
  }

  async function start(job: Job): Promise<void> {
    try {
      job.resolve(await job.task());
    } catch (error) {
      const pause = error instanceof ApiError && error.code === 'RATE_LIMITED' ? (error.retryAfterMs ?? DEFAULT_PAUSE_MS) : null;
      if (pause !== null && pause <= MAX_PAUSE_MS && job.refusals < RETRIES && !closed) {
        job.refusals += 1;
        pausedUntil = Math.max(pausedUntil, deps.now() + pause);
        queue.unshift(job);
        pump();
        return;
      }
      job.reject(error);
    }
  }

  return {
    run<T>(task: () => Promise<T>, priority: Priority = 'low', cancelled: () => boolean = () => false): Promise<T> {
      if (closed) return Promise.reject(cancelledError());
      return new Promise<T>((resolve, reject) => {
        queue.push({ task, priority, cancelled, refusals: 0, resolve: resolve as (v: unknown) => void, reject });
        pump();
      });
    },
    close() {
      closed = true;
      for (const job of queue.splice(0)) job.reject(cancelledError());
    },
    open() {
      closed = false;
    },
  };
}
