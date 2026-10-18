// The hero's replay, wired (brief 8.1; slice 5 Q2). useRecording loads the lot and its ledger,
// newest page first, through the page's scheduler; a page that fails ends the loading, and the
// replay starts from what loaded with one honest row for the rest. usePlayback runs the replay's
// own clock: it starts once the board is half in view, pauses while the board is off screen or
// the tab is hidden, plays once and stops on the result; "Replay again" starts it over.
import { useEffect, useState, type RefObject } from 'react';

import type { AuctionDetail, HistoryBid } from '../lib/contract';
import { LEDGER_PAGE, type LandingApi } from './api';
import { advance, LEDGER_CAP, record, type Playhead, type Recording } from './replay';

const MAX_PAGES = LEDGER_CAP / LEDGER_PAGE;
export const FRAME_MS = 250; // the board repaints four times a second, like the room's countdown

export type RecordingState =
  | { kind: 'loading'; lot: AuctionDetail | null }
  | { kind: 'ready'; lot: AuctionDetail; rec: Recording }
  | { kind: 'failed' };

export function useRecording(demo: LandingApi, auctionId: string): RecordingState {
  const [state, setState] = useState<RecordingState>({ kind: 'loading', lot: null });
  useEffect(() => {
    let gone = false;
    void (async () => {
      let lot: AuctionDetail;
      try {
        lot = await demo.detail(auctionId);
      } catch {
        if (!gone) setState({ kind: 'failed' });
        return;
      }
      if (gone) return;
      setState({ kind: 'loading', lot });
      const pages: HistoryBid[][] = [];
      let before: number | undefined;
      for (let i = 0; i < MAX_PAGES; i++) {
        try {
          const page = await demo.ledgerPage(auctionId, before);
          pages.push(page.items);
          if (page.next_before === null) break;
          before = page.next_before;
        } catch {
          break; // the older bids stay one honest row
        }
        if (gone) return;
      }
      if (gone) return;
      const rec = record(pages, lot);
      setState(rec.bids.length > 0 ? { kind: 'ready', lot, rec } : { kind: 'failed' });
    })();
    return () => {
      gone = true;
    };
  }, [demo, auctionId]);
  return state;
}

export type Playback = {
  /** replay time: the recorded instant the board shows */
  t: number;
  elapsedMs: number;
  durationMs: number;
  finished: boolean;
  again: () => void;
};

export function usePlayback(rec: Recording | null, target: RefObject<HTMLElement | null>): Playback {
  const [started, setStarted] = useState(false);
  const [inView, setInView] = useState(false);
  const [visible, setVisible] = useState(() => !document.hidden);
  const [head, setHead] = useState<Playhead>({ elapsedMs: 0, lastMs: null });
  const durationMs = rec === null ? 0 : rec.endMs - rec.startMs;
  const finished = rec !== null && head.elapsedMs >= durationMs;

  useEffect(() => {
    const element = target.current;
    if (element === null || rec === null) return;
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry === undefined) return;
        setInView(entry.isIntersecting);
        if (entry.intersectionRatio >= 0.5) setStarted(true); // autoplay once, half in view
      },
      { threshold: [0, 0.5] },
    );
    observer.observe(element);
    return () => observer.disconnect();
  }, [target, rec]);

  useEffect(() => {
    const onChange = () => setVisible(!document.hidden);
    document.addEventListener('visibilitychange', onChange);
    return () => document.removeEventListener('visibilitychange', onChange);
  }, []);

  const playing = rec !== null && started && inView && visible && !finished;
  useEffect(() => {
    const step = () => setHead((h) => advance(h, performance.now(), playing));
    step();
    if (!playing) return;
    const id = setInterval(step, FRAME_MS);
    return () => clearInterval(id);
  }, [playing]);

  const elapsedMs = Math.min(head.elapsedMs, durationMs);
  return {
    t: rec === null ? 0 : rec.startMs + elapsedMs,
    elapsedMs,
    durationMs,
    finished,
    again: () => {
      setHead({ elapsedMs: 0, lastMs: null });
      setStarted(true);
    },
  };
}
