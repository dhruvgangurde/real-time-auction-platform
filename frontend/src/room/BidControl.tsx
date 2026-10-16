// The bid control (brief 9.4, UI-5, UI-7, NFR-35): the minimum next bid prefilled, quick choices,
// an amount above three times the current bid confirmed inline (role=alertdialog, BR-14), Enter
// submits and Escape resets, and focus stays in the field after every result. It calls
// session.placeBid, which goes through the reducer and the one arbitration path; no bidding logic
// lives here. The panel also carries the non-bidding states of brief 9.3 (anonymous, scheduled,
// paused, closing, won, signed out, not verified, suspended).
import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router';

import { formatDay, formatLocal } from '../lib/clock';
import type { UserProfile } from '../lib/contract';
import { formatINR, parseRupees } from '../lib/money';
import { canBid, type RoomState } from '../live/room';
import { errorMessage, useStrings } from '../strings/catalogue';
import { blockedBy } from './account';
import styles from './room.module.css';
import type { RoomSession } from './session';

/** Whole rupees of a paise amount for a text field: "12,000" (parseRupees reads it back). */
const rupeeField = (paise: number) => formatINR(paise).replace('₹', '');

type Props = {
  state: RoomState;
  session: RoomSession | null;
  profile: UserProfile | undefined;
  signedIn: boolean;
  startTimeMs: number | null;
  /** the won lot's payment time, from the settlement; null shows Pay */
  paidAtMs: number | null;
  onResend: () => void;
};

export function BidControl({ state, session, profile, signedIn, startTimeMs, paidAtMs, onResend }: Props) {
  const s = useStrings();
  const b = s.roomUi.bid;
  const rupee = s.roomUi.rupee;
  const inputRef = useRef<HTMLInputElement>(null);
  const minNext = state.minNext ?? state.startPrice ?? 0;
  const [value, setValue] = useState(() => rupeeField(minNext));
  const [prevMin, setPrevMin] = useState(minNext);
  const [invalid, setInvalid] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<number | null>(null);

  // a new minimum (a bid, an outbid): prefill it and drop a half-typed amount (brief 9.3 "outbid")
  if (prevMin !== minNext) {
    setPrevMin(minNext);
    setValue(rupeeField(minNext));
    setInvalid(null);
    setConfirm(null);
  }

  const focusField = () => inputRef.current?.focus();
  // focus stays in the field after any own result (NFR-35)
  useEffect(() => {
    if (state.last !== null || state.own.outbidAt !== null) focusField();
  }, [state.last, state.own.outbidAt]);

  const current = state.price ?? state.startPrice ?? 0;
  const limit = (state.startPrice ?? 0) * 100;

  function submit(amount: number): void {
    setConfirm(null);
    session?.placeBid(amount);
    focusField();
  }

  function trySubmit(): void {
    const parsed = parseRupees(value);
    if (!parsed.ok) {
      setInvalid(b.invalid);
      focusField();
      return;
    }
    if (parsed.paise > limit) {
      setInvalid(b.aboveLimit(formatINR(limit)));
      focusField();
      return;
    }
    setInvalid(null);
    if (parsed.paise > current * 3) {
      setConfirm(parsed.paise);
      return;
    }
    submit(parsed.paise);
  }

  const rejected = state.last?.kind === 'rejected' ? state.last : null;
  const blocked = blockedBy(state, profile);
  const time = (ms: number | null) => (ms === null ? '' : formatLocal(ms, { seconds: true }));

  // ---- the states that replace the bid form (brief 9.3)
  if (blocked === 'suspended') return panel(b.suspendedHead, b.suspendedNote);
  if (blocked === 'unverified') {
    return panel(b.verifyHead, b.verifyNote, (
      <div className={styles.actions}>
        <button type="button" className={styles.btnQuiet} onClick={onResend}>
          {b.resend}
        </button>
      </div>
    ));
  }
  // after 4004 the token is forgotten, so this comes before the anonymous panel
  if (state.closedCode === 4004) {
    return panel(b.signedOutHead, b.signedOutNote, (
      <div className={styles.actions}>
        <Link className={styles.btnPrimary} to="/sign-in" state={{ from: `/auctions/${state.auctionId}` }}>
          {s.nav.signIn}
        </Link>
      </div>
    ));
  }
  if (!signedIn) {
    return (
      <div className={styles.panel}>
        <h2>{b.anonHeading}</h2>
        <p className={styles.hint}>{b.minHint(formatINR(minNext))}</p>
        <div className={styles.actions}>
          <Link className={styles.btnPrimary} to="/sign-in" state={{ from: `/auctions/${state.auctionId}` }}>
            {b.signInToBid}
          </Link>
        </div>
        <p className={styles.hint}>
          {b.registerPrompt} <Link className={styles.link} to="/register">{b.register}</Link>
        </p>
      </div>
    );
  }
  if (state.own.result === 'won') {
    // the payment page is its own document with its own CSP: a full load, for Pay and the receipt
    const page = `/auctions/${state.auctionId}/pay`;
    return (
      <div className={styles.panel}>
        {paidAtMs === null ? (
          <div className={styles.actions}>
            <a className={styles.btnPrimary} href={page}>
              {b.pay(formatINR(state.finalPrice ?? state.price ?? 0))}
            </a>
          </div>
        ) : (
          <>
            <p className={`${styles.hint} tabular`}>{b.paidOn(`${formatDay(paidAtMs)}, ${formatLocal(paidAtMs)}`)}</p>
            <div className={styles.actions}>
              <a className={styles.btnQuiet} href={page}>
                {b.receipt}
              </a>
            </div>
          </>
        )}
      </div>
    );
  }
  if (state.terminal !== null) return null; // lost, no sale, cancelled: nothing to do next
  if (state.status === 'scheduled') {
    return (
      <div className={styles.panel}>
        <h2>{b.heading}</h2>
        <div className={`${styles.fieldOff} tabular`} aria-hidden="true">
          <span className={styles.cur}>{rupee}</span>
          {rupeeField(state.startPrice ?? 0)}
        </div>
        <div className={styles.actions}>
          <span className={styles.btnDisabled} aria-disabled="true">
            {b.opensAt(time(startTimeMs))}
          </span>
        </div>
      </div>
    );
  }
  if (state.degraded) {
    return offForm(b.paused, b.pausedNote);
  }
  if (state.status === 'closing') {
    return (
      <div className={styles.panel}>
        <h2>{b.heading}</h2>
        <div className={styles.actions}>
          <span className={styles.btnDisabled} aria-disabled="true">
            {b.ended}
          </span>
        </div>
      </div>
    );
  }
  if (state.own.leading && state.selfOutbidBlocked) {
    return (
      <div className={styles.panel}>
        <h2>{b.heading}</h2>
        {offField(minNext)}
        <div className={styles.actions}>
          <span className={styles.btnDisabled} aria-disabled="true">
            {b.youLead}
          </span>
        </div>
        <p className={styles.hint}>{b.youLeadNote}</p>
      </div>
    );
  }

  // ---- the live bid form
  const pending = state.pending?.kind === 'bid' ? state.pending : null;
  const retrying = rejected?.code === 'SERVICE_UNAVAILABLE';
  let message: React.ReactNode = null;
  if (retrying) {
    message = (
      <div className={styles.msgInfo} role="status">
        <b>{b.retryingHead}</b>
        <span className={styles.sub}>{b.retryingNote}</span>
      </div>
    );
  } else if (state.rateLimited) {
    message = (
      <div className={styles.msgErr} role="status">
        <b>{errorMessage(s, 'RATE_LIMITED')}</b>
        <span className={styles.sub}>{b.rateLimitedNote(formatINR(minNext))}</span>
      </div>
    );
  } else if (rejected !== null) {
    message = (
      <div className={styles.msgErr} role="status">
        <b>{errorMessage(s, rejected.code)}</b>
        {rejected.minNext !== null ? <span className={styles.sub}>{b.rejectedMin(formatINR(rejected.minNext))}</span> : null}
      </div>
    );
  } else if (state.last?.kind === 'accepted') {
    message = (
      <div className={styles.msgOk} role="status">
        <b>{b.acceptedHead(state.last.sequence, formatINR(state.last.amount))}</b>
        <span className={styles.sub}>{b.youLead}</span>
      </div>
    );
  }

  const parsedNow = parseRupees(value);
  const buttonAmount = parsedNow.ok ? parsedNow.paise : minNext;
  const chips = canBid(state) && confirm === null ? [minNext, minNext + (state.minIncrement ?? 0), minNext + 5 * (state.minIncrement ?? 0)] : [];
  return (
    <div className={styles.panel}>
      <h2>{b.heading}</h2>
      <label className="sr-only" htmlFor="bid-amount">
        {b.fieldLabel}
      </label>
      {pending !== null ? (
        offField(pending.amount)
      ) : (
        <div className={`${styles.field} tabular`}>
          <span className={styles.cur}>{rupee}</span>
          <input
            ref={inputRef}
            id="bid-amount"
            inputMode="numeric"
            autoComplete="off"
            aria-describedby="bid-min"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault();
                trySubmit();
              } else if (e.key === 'Escape') {
                setValue(rupeeField(minNext));
                setConfirm(null);
                setInvalid(null);
              }
            }}
          />
        </div>
      )}
      <p className={styles.hint} id="bid-min">
        {b.minHint(formatINR(minNext))}
      </p>
      {chips.length > 0 ? (
        <div className={styles.quick}>
          {chips.map((amount, i) => (
            <button
              key={amount}
              type="button"
              className={styles.chip}
              aria-label={i === 0 ? b.theMinimum(formatINR(amount)) : formatINR(amount)}
              onClick={() => {
                setValue(rupeeField(amount));
                focusField();
              }}
            >
              {formatINR(amount)}
            </button>
          ))}
        </div>
      ) : null}
      {invalid !== null ? (
        <div className={styles.msgErr} role="alert">
          <b>{invalid}</b>
        </div>
      ) : null}
      {confirm !== null ? (
        <>
          <div className={styles.msgInfo} role="alertdialog" aria-labelledby="confirm-3x">
            <b id="confirm-3x">{b.over3x(formatINR(confirm), formatINR(current))}</b>
            <span className={styles.sub}>{b.over3xNote(formatINR(limit))}</span>
          </div>
          <div className={styles.actions}>
            <button type="button" className={styles.btnPrimary} onClick={() => submit(confirm)}>
              {b.confirm(formatINR(confirm))}
            </button>
            <button
              type="button"
              className={styles.btnQuiet}
              onClick={() => {
                setConfirm(null);
                focusField();
              }}
            >
              {b.changeAmount}
            </button>
          </div>
        </>
      ) : pending !== null ? (
        <div className={styles.actions}>
          <span className={styles.btnDisabled} aria-disabled="true">
            {retrying ? b.checking(formatINR(pending.amount)) : b.sending(formatINR(pending.amount))}
          </span>
        </div>
      ) : (
        <div className={styles.actions}>
          <button type="button" className={styles.btnPrimary} onClick={trySubmit}>
            {b.bid(formatINR(buttonAmount))}
          </button>
        </div>
      )}
      {message}
      {pending !== null && !retrying ? <p className={styles.pend}>{b.pendingNote}</p> : null}
    </div>
  );

  function panel(head: string, note: string, extra?: React.ReactNode) {
    return (
      <div className={styles.panel}>
        <h2>{head}</h2>
        <p className={styles.hint}>{note}</p>
        {extra}
      </div>
    );
  }

  function offForm(action: string, note: string) {
    return (
      <div className={styles.panel}>
        <h2>{b.heading}</h2>
        {offField(minNext)}
        <div className={styles.actions}>
          <span className={styles.btnDisabled} aria-disabled="true">
            {action}
          </span>
        </div>
        <p className={styles.hint}>{note}</p>
      </div>
    );
  }

  function offField(paise: number) {
    return (
      <div className={`${styles.fieldOff} tabular`} aria-hidden="true">
        <span className={styles.cur}>{rupee}</span>
        {rupeeField(paise)}
      </div>
    );
  }
}
