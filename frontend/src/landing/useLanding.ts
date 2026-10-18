// The landing's shared data (brief 8.1, 8.3): one API with one scheduler for the whole page, the
// demo overview (GET /demo, read again after a run), and the newest live real lot.
import { useEffect, useMemo, useState } from 'react';

import type { Api } from '../api/client';
import type { AuctionCard } from '../lib/contract';
import { landingApi, type LandingApi } from './api';
import type { DemoOverview } from './contract';
import { createScheduler, type SchedulerDeps } from './scheduler';

const CLOCK: SchedulerDeps = { now: () => Date.now(), setTimer: (fn, ms) => setTimeout(fn, ms) };

function build(api: Api) {
  const scheduler = createScheduler(CLOCK);
  return { scheduler, demo: landingApi(api, scheduler) };
}

export function useLandingApi(api: Api): LandingApi {
  const built = useMemo(() => build(api), [api]);
  useEffect(() => {
    built.scheduler.open();
    return () => built.scheduler.close(); // requests still queued are dropped, never sent
  }, [built]);
  return built.demo;
}

export type OverviewState = { kind: 'loading' } | { kind: 'off' } | { kind: 'on'; overview: DemoOverview } | { kind: 'failed' };

/** GET /demo once, and again on refresh(); a refresh keeps the last answer on screen meanwhile. */
export function useOverview(demo: LandingApi): [OverviewState, () => void] {
  const [state, setState] = useState<OverviewState>({ kind: 'loading' });
  const [round, setRound] = useState(0);
  useEffect(() => {
    let gone = false;
    demo.overview().then(
      (overview) => {
        if (!gone) setState(overview === null ? { kind: 'off' } : { kind: 'on', overview });
      },
      () => {
        if (!gone) setState({ kind: 'failed' });
      },
    );
    return () => {
      gone = true;
    };
  }, [demo, round]);
  return [state, () => setRound((n) => n + 1)];
}

export type LiveLot = { kind: 'loading' } | { kind: 'done'; lot: AuctionCard | null };

/** The newest live real lot for "Watch a live auction" (slice 5 Q4); none: the action is omitted. */
export function useNewestLive(demo: LandingApi): LiveLot {
  const [state, setState] = useState<LiveLot>({ kind: 'loading' });
  useEffect(() => {
    let gone = false;
    demo.newestLive().then(
      (lot) => {
        if (!gone) setState({ kind: 'done', lot });
      },
      () => {
        if (!gone) setState({ kind: 'done', lot: null });
      },
    );
    return () => {
      gone = true;
    };
  }, [demo]);
  return state;
}
