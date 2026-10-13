// The proxy (maximum) control (brief 9.4, UI-6, BR-7): a disclosure that sets or raises the
// viewer's maximum, which is shown back only to its owner ("Only you can see this"). It calls
// session.setProxy; the reducer arms it. The maximum is never shown to anyone else, in any state.
import { useRef, useState } from 'react';

import { formatLocal } from '../lib/clock';
import { formatINR, parseRupees } from '../lib/money';
import { type RoomState } from '../live/room';
import { useStrings } from '../strings/catalogue';
import styles from './room.module.css';
import type { RoomSession } from './session';

const rupeeField = (paise: number) => formatINR(paise).replace('₹', '');

export function ProxyControl({ state, session, startTimeMs }: { state: RoomState; session: RoomSession | null; startTimeMs: number | null }) {
  const s = useStrings();
  const p = s.roomUi.proxy;
  const rupee = s.roomUi.rupee;
  const inputRef = useRef<HTMLInputElement>(null);
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState('');
  const minNext = state.minNext ?? state.startPrice ?? 0;

  if (state.status === 'scheduled') {
    return (
      <div className={`${styles.panel} ${styles.proxyPanel}`}>
        <h2>{p.set}</h2>
        <p className={styles.privacy}>{p.scheduledNote(startTimeMs === null ? '' : formatLocal(startTimeMs, { seconds: true }))}</p>
      </div>
    );
  }
  // only while the viewer may act (live, signed in, not degraded, not terminal); the bid form's
  // own replacement states (not verified, suspended, signed out) remove the proxy too
  if (state.status !== 'live' || state.terminal !== null || state.degraded || state.closedCode === 4004) return null;

  const active = state.own.maximumActive && state.own.maximum !== null;
  const exhausted = !state.own.maximumActive && state.own.maximum !== null;

  function set(): void {
    const parsed = parseRupees(value);
    if (!parsed.ok) {
      inputRef.current?.focus();
      return;
    }
    session?.setProxy(parsed.paise);
    setOpen(false);
    setValue('');
  }

  const form = open ? (
    <div className={styles.form}>
      <label className="sr-only" htmlFor="proxy-amount">
        {p.fieldLabel}
      </label>
      <div className={`${styles.field} tabular`}>
        <span className={styles.cur}>{rupee}</span>
        <input
          ref={inputRef}
          id="proxy-amount"
          inputMode="numeric"
          autoComplete="off"
          placeholder={rupeeField(minNext)}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault();
              set();
            } else if (e.key === 'Escape') {
              setOpen(false);
            }
          }}
        />
      </div>
      <div className={styles.actions}>
        <button type="button" className={styles.btnPrimary} onClick={set}>
          {p.confirm}
        </button>
      </div>
    </div>
  ) : null;

  if (active) {
    return (
      <div className={`${styles.panel} ${styles.proxyPanel}`}>
        <div className={styles.disclose}>
          <h2>{p.yours}</h2>
          <button type="button" className={styles.link} onClick={() => setOpen((o) => !o)}>
            {p.raise}
          </button>
        </div>
        <div className={styles.own}>
          <span className={`${styles.v} tabular`}>{formatINR(state.own.maximum ?? 0)}</span>
          <span className={styles.only}>{p.only}</span>
        </div>
        <p className={styles.privacy}>{p.activeNote(formatINR(state.own.maximum ?? 0))}</p>
        {form}
      </div>
    );
  }
  if (exhausted) {
    return (
      <div className={`${styles.panel} ${styles.proxyPanel}`}>
        <div className={styles.disclose}>
          <h2>{p.reached}</h2>
          <button type="button" className={styles.link} onClick={() => setOpen((o) => !o)}>
            {p.setNew}
          </button>
        </div>
        <p className={styles.privacy}>{p.reachedNote}</p>
        {form}
      </div>
    );
  }
  return (
    <div className={`${styles.panel} ${styles.proxyPanel}`}>
      <div className={styles.disclose}>
        <h2>{p.set}</h2>
        <button type="button" className={styles.link} onClick={() => setOpen((o) => !o)}>
          {p.setAction}
        </button>
      </div>
      <p className={styles.privacy}>{p.intro}</p>
      {form}
    </div>
  );
}
