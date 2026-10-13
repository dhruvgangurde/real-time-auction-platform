// The countdown (UI-4, OR-1): remaining time from end_time_ms and the server clock only; a local
// timer repaints. Milestones at 5 min, 1 min, 30 s and 10 s go to the polite region (brief 11.4);
// zero turns the room to Closing until a snapshot or an extension says otherwise. The extension
// note (FR-55) stays 8 s, then condenses (brief 6). Never pulses, never turns red.
import { useEffect, useReducer, useRef, useState } from 'react';

import { useApp } from '../app/context';
import { formatLocal, formatRemaining, formatWindow } from '../lib/clock';
import { isStale, type RoomState } from '../live/room';
import { useStrings } from '../strings/catalogue';
import styles from './room.module.css';
import type { RoomSession } from './session';
import { crossed } from './time';
import { Timeline } from './Timeline';

const NOTE_MS = 8_000;

export function Countdown({
  state,
  session,
  startTimeMs,
  windowMs,
  extensionMs,
}: {
  state: RoomState;
  session: RoomSession | null;
  startTimeMs: number | null;
  windowMs: number;
  extensionMs: number;
}) {
  const s = useStrings();
  const c = s.roomUi.clock;
  const { clock } = useApp();
  const [, repaint] = useReducer((n: number) => n + 1, 0);
  useEffect(() => {
    const id = setInterval(repaint, 250);
    return () => clearInterval(id);
  }, []);

  const end = state.endTimeMs;
  const remaining = end === null ? 0 : clock.remainingMs(end);
  const live = state.status === 'live' && state.terminal === null;
  const prev = useRef<{ end: number; remaining: number } | null>(null);
  useEffect(() => {
    if (end === null || !live) {
      prev.current = null;
      return;
    }
    const before = prev.current;
    prev.current = { end, remaining };
    if (before === null || before.end !== end) return; // a new end: milestones start over
    for (const t of crossed(before.remaining, remaining)) {
      session?.announcePolite(t >= 60_000 ? s.announce.minutesLeft(t / 60_000) : s.announce.secondsLeft(t / 1000));
    }
    if (remaining === 0 && before.remaining > 0) session?.countdownZero();
  });

  // the latest extension, shown in full for 8 s (noted: the extension count being explained)
  const [seen, setSeen] = useState(state.extensionCount);
  const [noted, setNoted] = useState<number | null>(null);
  if (seen !== state.extensionCount) {
    setSeen(state.extensionCount);
    setNoted(state.extensionCount > seen && state.previousEndMs !== null ? state.extensionCount : null);
  }
  useEffect(() => {
    if (noted === null) return;
    const id = setTimeout(() => setNoted(null), NOTE_MS);
    return () => clearTimeout(id);
  }, [noted]);
  const recent = noted !== null && state.previousEndMs !== null;

  if (state.terminal !== null || state.status === 'draft' || state.status === null) return null;
  const time = (ms: number) => formatLocal(ms, { seconds: true });
  if (state.status === 'scheduled') {
    if (startTimeMs === null || end === null) return null;
    return (
      <div className={styles.clock}>
        <span className={`${styles.clockTime} tabular`}>{formatRemaining(clock.remainingMs(startTimeMs))}</span>
        <span className={styles.clockNote}>{c.untilOpens(time(startTimeMs), time(end))}</span>
      </div>
    );
  }
  if (end === null) return null;
  const window = formatWindow(windowMs, s.roomUi.units);
  const extension = formatRemaining(extensionMs);
  const closing = state.status === 'closing' || remaining === 0;
  let note: React.ReactNode;
  let final = false;
  if (closing) {
    note = c.closing;
    final = true;
  } else if (state.finalExtension) {
    note = c.cap(time(end));
    final = true;
  } else if (recent && state.previousEndMs !== null) {
    note = (
      <>
        {c.endsLabel} <s>{time(state.previousEndMs)}</s>{' '}
        <span key={state.extensionCount} className={styles.swap}>
          {c.timeStop(time(end))}
        </span>{' '}
        {c.extendedWhy(extension, window, state.extensionCount)}
      </>
    );
  } else {
    note = [c.ends(time(end)), state.extensionCount > 0 ? c.extendedShort(state.extensionCount) : c.lastMinute(window, extension)].join(' ');
  }
  const stale = isStale(state);
  return (
    <>
      <div className={styles.clock}>
        <span className={`${styles.clockTime} tabular`}>{formatRemaining(remaining)}</span>
        <span className={final ? styles.clockNoteFinal : styles.clockNote}>
          {note}
          {stale ? ' ' : null}
          {stale ? c.mayExtended : null}
        </span>
      </div>
      {closing ? null : (
        <Timeline
          nowMs={clock.now()}
          endMs={end}
          oldEndMs={recent ? state.previousEndMs : null}
          windowMs={windowMs}
          extensionMs={extensionMs}
          capped={state.finalExtension}
          sequence={state.sequence}
        />
      )}
    </>
  );
}
