// The payment page, /auctions/:id/pay (brief 9.5; FR-65 to FR-69, DC-10, SI-3). Its own document
// with its own CSP, the only one that allows Razorpay (brief 13 item 25); no router, no socket. The
// settlement says who may pay (flow.ts); "Pay" loads checkout.js once and opens Razorpay's window
// on the server's order; its success hands back ids that are posted to /confirm, and Paid appears
// only when the settlement reads paid. Card and UPI entry happen in Razorpay's window only.
import { useQuery } from '@tanstack/react-query';
import { useEffect, useReducer, useRef, useState } from 'react';

import { ApiError } from '../api/client';
import { useApp, useAuthReady, useAuthUnreachable, useToken } from '../app/context';
import { Unreachable } from '../app/Unreachable';
import { formatDay, formatLocal } from '../lib/clock';
import { formatINR } from '../lib/money';
import { errorMessage, useStrings } from '../strings/catalogue';
import { payEnIN as p } from '../strings/pay-en-IN';
import styles from './pay.module.css';
import { payReducer, type PayState } from './flow';
import { checkoutIds, checkoutOptions, createLoader, type RazorpayCtor } from './razorpay';
import { sellerLines } from './seller';

const loadCheckout = createLoader(document, window as { Razorpay?: RazorpayCtor });
const POLL_FAST_MS = 2_000;
const POLL_SLOW_MS = 10_000;
const SLOW_AFTER_MS = 60_000;
// the title beside the photo names the lot, so the photo is decorative (empty alt)
const DECORATIVE = '';

/** The auction id from /auctions/<uuid>/pay; nginx serves this document only for such a path. */
export function auctionIdFrom(pathname: string): string {
  return /^\/auctions\/([0-9a-f-]{36})\/pay$/.exec(pathname)?.[1] ?? '';
}

const codeOf = (error: unknown): string => (error instanceof ApiError ? error.code : 'SERVICE_UNAVAILABLE');
const at = (ms: number) => `${formatDay(ms)}, ${formatLocal(ms)}`;

export function PayPage() {
  const s = useStrings();
  const { api } = useApp();
  const id = auctionIdFrom(window.location.pathname);
  const ready = useAuthReady();
  const signedIn = useToken() !== null;
  const unreachable = useAuthUnreachable();
  const [state, dispatch] = useReducer(payReducer, { kind: 'loading' } as PayState);
  const [slow, setSlow] = useState(false);
  const action = useRef<HTMLButtonElement>(null);
  const said = useRef<HTMLParagraphElement>(null);
  const detail = useQuery({ queryKey: ['auction', id], queryFn: () => api.detail(id), enabled: id !== '', retry: false });
  const title = detail.data?.title ?? '';
  const photo = detail.data === undefined ? undefined : [...detail.data.images].sort((a, b) => a.position - b.position)[0];

  // who is here, and what the settlement says
  useEffect(() => {
    if (!ready) return;
    if (!signedIn) {
      dispatch({ type: 'session', state: unreachable ? 'unreachable' : 'signedOut' });
      return;
    }
    if (state.kind === 'signedOut' || state.kind === 'unreachable') {
      dispatch({ type: 'session', state: 'loading' });
      return;
    }
    if (state.kind !== 'loading') return;
    let cancelled = false;
    Promise.all([api.settlement(id), api.me()]).then(
      ([settlement, me]) => {
        if (!cancelled) dispatch({ type: 'loaded', settlement, viewerId: me.id });
      },
      (error: unknown) => {
        if (!cancelled) dispatch({ type: 'loadFailed', code: codeOf(error) });
      },
    );
    return () => {
      cancelled = true;
    };
  }, [ready, signedIn, unreachable, state.kind, api, id]);

  // confirming: read the settlement every 2 s for a minute, then every 10 s, until it says paid
  const confirming = state.kind === 'confirming';
  useEffect(() => {
    if (!confirming) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const started = Date.now();
    const slowTimer = setTimeout(() => setSlow(true), SLOW_AFTER_MS);
    const tick = async () => {
      try {
        const settlement = await api.settlement(id);
        if (!stopped) dispatch({ type: 'polled', settlement });
      } catch {
        /* read again on the next tick */
      }
      if (!stopped) timer = setTimeout(() => void tick(), Date.now() - started < SLOW_AFTER_MS ? POLL_FAST_MS : POLL_SLOW_MS);
    };
    timer = setTimeout(() => void tick(), POLL_FAST_MS);
    return () => {
      stopped = true;
      clearTimeout(timer);
      clearTimeout(slowTimer);
      setSlow(false);
    };
  }, [confirming, api, id]);

  // back from the window without paying, or after a failure: the button has the focus again; a
  // start refused for good takes the button away, so its reason gets the focus. Only then: on load
  // the page is read from the top, the lot before the button
  const kind = state.kind;
  const previousKind = useRef(kind);
  useEffect(() => {
    const before = previousKind.current;
    previousKind.current = kind;
    if (before !== 'open' && before !== 'opening') return;
    if (kind === 'due' || kind === 'failed') action.current?.focus({ preventScroll: true });
    if (kind === 'belowMinimum') said.current?.focus({ preventScroll: true });
  }, [kind]);

  async function confirm(response: unknown) {
    dispatch({ type: 'authorized' });
    const ids = checkoutIds(response);
    if (ids === null) {
      dispatch({ type: 'confirmFailed', code: 'INVALID_SIGNATURE' });
      return;
    }
    try {
      const result = await api.confirmPayment(id, ids);
      dispatch({ type: 'confirmed', status: result.status });
    } catch (error) {
      dispatch({ type: 'confirmFailed', code: codeOf(error) });
    }
  }

  async function pay() {
    if (state.kind !== 'due' && state.kind !== 'failed') return;
    dispatch({ type: 'pay' });
    const [order, ctor] = await Promise.allSettled([api.startPayment(id), loadCheckout()]);
    if (order.status === 'rejected') {
      const error: unknown = order.reason;
      dispatch({ type: 'startFailed', code: codeOf(error), ...(error instanceof ApiError && error.retryAfterMs !== undefined ? { retryAfterMs: error.retryAfterMs } : {}) });
      return;
    }
    if (ctor.status === 'rejected') {
      dispatch({ type: 'scriptFailed' });
      return;
    }
    const Razorpay = ctor.value;
    const color = getComputedStyle(document.documentElement).getPropertyValue('--ink').trim();
    const checkout = new Razorpay(
      checkoutOptions(order.value, {
        name: s.app.name,
        description: title,
        color,
        handler: (response) => void confirm(response),
        ondismiss: () => dispatch({ type: 'dismissed' }),
      }),
    );
    checkout.on('payment.failed', () => dispatch({ type: 'gatewayFailed' }));
    checkout.open();
    dispatch({ type: 'opened' });
  }

  const notice = (code: string, retryAfterMs: number | null): string => {
    if (code === 'DISMISSED') return p.dismissed;
    if (code === 'SCRIPT') return p.scriptFailed;
    if (code === 'SERVICE_UNAVAILABLE' || code === 'INTERNAL_ERROR') return p.unavailable;
    if (code === 'RATE_LIMITED') return retryAfterMs === null ? p.rateLimited(60) : p.rateLimited(Math.ceil(retryAfterMs / 1000));
    return errorMessage(s, code);
  };

  let panel;
  switch (state.kind) {
    case 'loading':
      panel = <p className={styles.quiet}>{s.common.loading}</p>;
      break;
    case 'unreachable':
      panel = <Unreachable />;
      break;
    case 'signedOut':
      panel = (
        <>
          <h2 className={styles.head}>{p.signInTitle}</h2>
          <p className={styles.text}>{p.signInNote}</p>
          <a className={styles.primary} href={`/sign-in?next=${encodeURIComponent(window.location.pathname)}`}>
            {p.signIn}
          </a>
        </>
      );
      break;
    case 'error':
      panel = (
        <>
          <p className={styles.text}>{notice(state.code, null)}</p>
          <button type="button" className={styles.quietButton} onClick={() => dispatch({ type: 'session', state: 'loading' })}>
            {s.common.tryAgain}
          </button>
        </>
      );
      break;
    case 'refused':
      panel = <p className={styles.text}>{state.code === 'NOT_FOUND' ? p.notFound : p.notWinner}</p>;
      break;
    case 'noSale':
      panel = <p className={styles.text}>{p.noSale}</p>;
      break;
    case 'status':
      panel = (
        <>
          <h2 className={styles.head}>{p.statusTitle}</h2>
          {sellerLines(state.settlement, p, at).map((line, i) => (
            <p key={line} className={i === 0 ? styles.text : `${styles.quiet} tabular`}>
              {line}
            </p>
          ))}
        </>
      );
      break;
    case 'due':
    case 'opening':
    case 'failed': {
      const busy = state.kind === 'opening';
      panel = (
        <>
          {state.kind === 'failed' ? (
            <>
              <h2 className={styles.head}>{p.failedTitle}</h2>
              <p className={styles.text}>{p.failed}</p>
            </>
          ) : (
            <p className={styles.text}>{p.wonFor(formatINR(state.amount))}</p>
          )}
          {state.kind === 'due' && state.lastFailed ? <p className={styles.warn}>{p.lastFailed}</p> : null}
          {state.kind === 'due' && state.notice !== null ? <p className={styles.warn}>{notice(state.notice, state.retryAfterMs)}</p> : null}
          <button ref={action} type="button" className={styles.primary} onClick={() => void pay()} aria-disabled={busy}>
            {busy ? p.opening : state.kind === 'failed' ? p.tryAgain : p.pay(formatINR(state.amount))}
          </button>
        </>
      );
      break;
    }
    case 'belowMinimum':
      panel = (
        <>
          <p className={styles.text}>{p.wonFor(formatINR(state.amount))}</p>
          <p ref={said} tabIndex={-1} className={styles.warn}>
            {p.belowMinimum}
          </p>
        </>
      );
      break;
    case 'open':
      panel = <p className={styles.text}>{p.open}</p>;
      break;
    case 'confirming':
      panel = (
        <>
          <h2 className={styles.head}>{p.confirmingTitle}</h2>
          <p className={styles.text}>{state.unverified ? p.unverified : p.confirming}</p>
          <p className={styles.quiet}>{slow ? p.slow : p.confirmingNote}</p>
        </>
      );
      break;
    case 'paid':
      panel = (
        <>
          <h2 className={styles.head}>{p.paidTitle}</h2>
          <p className={styles.text}>{p.paid(formatINR(state.amount))}</p>
          {state.paidAtMs === null ? null : <p className={`${styles.quiet} tabular`}>{p.paidOn(at(state.paidAtMs))}</p>}
          {state.paymentId === null ? null : <p className={`${styles.quiet} tabular`}>{p.paymentId(state.paymentId)}</p>}
        </>
      );
      break;
  }

  return (
    <>
      <header className={styles.header}>
        <div className={styles.wrap}>
          <a className={styles.mark} href="/">
            {s.app.name}
          </a>
        </div>
      </header>
      <main id="main" className={styles.main}>
        <p>
          <a className={styles.back} href={id === '' ? '/' : `/auctions/${id}`}>
            {p.back}
          </a>
        </p>
        <h1 className={styles.title}>{p.title}</h1>
        {title === '' ? null : (
          <div className={styles.lot}>
            <div className={styles.thumb}>{photo === undefined ? null : <img src={photo.url} alt={DECORATIVE} />}</div>
            <div>
              <p className={styles.lotLabel}>{p.lotLabel}</p>
              <p className={styles.lotTitle}>{title}</p>
            </div>
          </div>
        )}
        <section className={styles.panel} aria-live="polite" data-state={state.kind}>
          {panel}
        </section>
        <p className={styles.note}>{p.testMode}</p>
      </main>
    </>
  );
}
