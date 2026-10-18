// "Try to break it" (brief 8.3): one button sends 50 simulated bidders at a fresh demo lot, then the
// ledger is checked in Postgres. Every state of the brief: Ready, Already running, Running,
// Settling, Verified, A check failed, Cooling down, Unavailable, plus a run that stopped before
// it could be checked. The status is polled every second while a run lasts; a failed check is
// shown first and as plainly as a pass, and every check carries its SQL under "How we checked".
import { useEffect, useReducer } from 'react';
import { Link } from 'react-router';

import { ApiError } from '../api/client';
import { useApp } from '../app/context';
import { useNow } from '../discover/useNow';
import { formatDecided, formatLocal, formatRemaining } from '../lib/clock';
import type { RecentBid } from '../lib/contract';
import { formatINR } from '../lib/money';
import { landingEnIN } from '../strings/landing-en-IN';
import type { LandingApi } from './api';
import { FINAL_STATES, parseCooldownField, type CheckResult, type StressStatus } from './contract';
import styles from './landing.module.css';
import { breakReducer, checkLine, INITIAL, phase, rejectedLine, type BreakState } from './stress';
import type { OverviewState } from './useLanding';

const t = landingEnIN.break;
const POLL_MS = 1_000; // brief 8.3: polled every 1 s

function Mark({ kind }: { kind: 'pass' | 'fail' | 'pending' }) {
  return (
    <svg className={styles.markIcon} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={kind === 'pending' ? 2 : 2.5} aria-hidden="true">
      {kind === 'pass' ? <path d="M4 12.5l5 5L20 6.5" /> : kind === 'fail' ? <path d="M6 6l12 12M18 6L6 18" /> : <circle cx="12" cy="12" r="8" />}
    </svg>
  );
}

function CheckRow({ check }: { check: CheckResult }) {
  const line = checkLine(check);
  const why = check.passed ? check.note : (t.failMeaning as Record<string, string | undefined>)[check.id];
  return (
    <div className={`${styles.check} ${check.passed ? styles.pass : styles.fail}`}>
      <Mark kind={check.passed ? 'pass' : 'fail'} />
      <span className={styles.what}>
        {check.label}
        {line === '' ? null : <span className={styles.found}>{line}</span>}
      </span>
      <span className={styles.res}>{check.passed ? t.pass : t.fail}</span>
      {why ? <span className={styles.why}>{why}</span> : null}
    </div>
  );
}

function WatcherRow({ status }: { status: StressStatus }) {
  const { received, expected } = status.watcher;
  if (received === null) {
    return (
      <div className={`${styles.check} ${styles.pending}`}>
        <Mark kind="pending" />
        <span className={styles.what}>{t.watcherPending}</span>
        <span className={styles.res} />
      </div>
    );
  }
  const passed = received === expected;
  return (
    <div className={`${styles.check} ${passed ? styles.pass : styles.fail}`}>
      <Mark kind={passed ? 'pass' : 'fail'} />
      <span className={styles.what}>{t.watcherLine(received, expected)}</span>
      <span className={styles.res}>{passed ? t.pass : t.fail}</span>
      {passed ? null : <span className={styles.why}>{t.watcherFail}</span>}
    </div>
  );
}

function RecentRows({ rows }: { rows: RecentBid[] }) {
  return (
    <table className={styles.ledger}>
      <caption className="sr-only">{landingEnIN.board.ledger.caption}</caption>
      <thead>
        <tr>
          <th className={styles.seq} scope="col">
            {landingEnIN.board.ledger.sequence}
          </th>
          <th scope="col">{landingEnIN.board.ledger.bidder}</th>
          <th className={styles.amount} scope="col">
            {landingEnIN.board.ledger.amount}
          </th>
          <th className={styles.time} scope="col">
            {landingEnIN.board.ledger.decided}
          </th>
        </tr>
      </thead>
      <tbody>
        {rows.slice(0, 6).map((b) => (
          <tr key={b.sequence_no}>
            <td className={`${styles.seq} tabular`}>{b.sequence_no}</td>
            <td>{b.bidder_alias}</td>
            <td className={`${styles.amount} tabular`}>{formatINR(b.amount)}</td>
            <td className={`${styles.time} tabular`}>{b.placed_at_ms === null ? null : formatDecided(b.placed_at_ms)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Counters({ status, leftMs }: { status: StressStatus | null; leftMs: number | null }) {
  const s = status;
  const rejected = s === null ? 0 : Object.values(s.submissions.rejected).reduce((a, b) => a + b, 0);
  const cell = (value: string, label: string, sub?: string) => (
    <div className={styles.counter}>
      <div className={`${styles.v} tabular`}>{value}</div>
      <div className={styles.k2}>{label}</div>
      {sub ? <div className={styles.k3}>{sub}</div> : null}
    </div>
  );
  return (
    <div className={styles.counters}>
      {cell(s === null ? '0' : t.counters.of(s.bots.connected, s.bots.planned), t.counters.connected)}
      {cell(String(s?.submissions.sent ?? 0), t.counters.sent)}
      {cell(String(s?.submissions.accepted ?? 0), t.counters.accepted)}
      {cell(String(rejected), t.counters.rejected, s === null ? undefined : rejectedLine(s.submissions.rejected))}
      {cell(s?.current_price == null ? '' : formatINR(s.current_price), t.counters.price)}
      {cell(leftMs === null ? '' : formatRemaining(leftMs), t.counters.left)}
    </div>
  );
}

function Results({ status }: { status: StressStatus }) {
  const checks = status.checks ?? [];
  const rejected = Object.values(status.submissions.rejected).reduce((a, b) => a + b, 0);
  const line = rejectedLine(status.submissions.rejected);
  const watcherFailed = status.watcher.received !== null && status.watcher.received !== status.watcher.expected;
  const postgres = (
    <>
      <div className={styles.sub}>{t.postgres}</div>
      {checks.map((c) => (
        <CheckRow key={c.id} check={c} />
      ))}
    </>
  );
  const watcher = (
    <>
      <div className={styles.sub}>{t.watcherHead}</div>
      <WatcherRow status={status} />
    </>
  );
  return (
    <div className={styles.checks}>
      <div className={styles.runHead}>
        <b>{t.lastRun(status.end_time_ms === null ? '' : formatLocal(status.end_time_ms, { seconds: true }), status.submissions.sent, status.submissions.accepted, rejected)}</b>
        {line === '' ? null : ' '}
        {line === '' ? null : t.rejectedBy(line)}
      </div>
      {watcherFailed ? watcher : postgres}
      {watcherFailed ? postgres : watcher}
      <div className={styles.links}>
        {status.auction_id === null ? null : (
          <Link className={styles.link} to={`/auctions/${status.auction_id}/bids`}>
            {t.ledger}
          </Link>
        )}
      </div>
      <details className={styles.how}>
        <summary className={styles.link}>{t.how}</summary>
        {checks.map((c) => (
          <div key={c.id} className={styles.sql}>
            <h3 className={styles.sqlTitle}>{c.label}</h3>
            <p className={styles.expected}>{t.expected(c.expected)}</p>
            <pre>
              <code>{c.sql}</code>
            </pre>
          </div>
        ))}
      </details>
    </div>
  );
}

function Panel({ state, leftMs }: { state: BreakState; leftMs: number | null }) {
  const status = state.status;
  switch (phase(state)) {
    case 'none':
      return (
        <div className={styles.checks}>
          <div className={styles.runHead}>
            <b>{t.willCheck}</b>
          </div>
          <ul className={styles.will}>
            {t.will.map((w) => (
              <li key={w}>{w}</li>
            ))}
          </ul>
        </div>
      );
    case 'running':
      return (
        <div>
          <div className={styles.runHead}>{status?.end_time_ms == null ? null : t.running(formatLocal(status.end_time_ms, { seconds: true }))}</div>
          <Counters status={status} leftMs={leftMs} />
          {status === null ? null : <RecentRows rows={status.recent_bids} />}
          <div className={`${styles.check} ${styles.pending}`}>
            <Mark kind="pending" />
            <span className={styles.what}>{t.watchingRow}</span>
            <span className={styles.res}>{t.watchingState}</span>
          </div>
        </div>
      );
    case 'settling':
      return (
        <div>
          <div className={styles.runHead}>
            <b>{t.settling}</b>
          </div>
          <Counters status={status} leftMs={0} />
        </div>
      );
    case 'verified':
    case 'failed':
      return status === null ? null : <Results status={status} />;
    case 'stopped':
      return (
        <div className={styles.checks}>
          <div className={styles.runHead}>
            <b>{t.stopped}</b>
          </div>
          {status?.reason ? <p className={styles.reason}>{t.stoppedReason(status.reason)}</p> : null}
        </div>
      );
  }
}

export function Break({ demo, overview, refresh }: { demo: LandingApi; overview: OverviewState; refresh: () => void }) {
  const { clock } = useApp();
  const [state, dispatch] = useReducer(breakReducer, INITIAL);
  const now = useNow(clock);

  // what GET /demo says, each time it is read
  useEffect(() => {
    if (overview.kind === 'off') dispatch({ type: 'overview', overview: null, nowMs: clock.now() });
    else if (overview.kind === 'on') dispatch({ type: 'overview', overview: overview.overview, nowMs: clock.now() });
    else if (overview.kind === 'failed') dispatch({ type: 'overviewFailed' });
  }, [overview, clock]);

  // once the cooldown has passed: read GET /demo again (so is it after a run without one)
  const control = state.control;
  const coolingUntil = control.kind === 'cooling' ? control.untilMs : null;
  useEffect(() => {
    if (coolingUntil !== null && now >= coolingUntil) {
      dispatch({ type: 'cooled' });
      refresh();
    }
  }, [now, coolingUntil, refresh]);

  // the run's status, every second while it lasts; the reducer keeps the highest version
  const watchingRun = control.kind === 'watching' ? state.runId : null;
  useEffect(() => {
    if (watchingRun === null) return;
    let gone = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      try {
        const status = await demo.stressStatus(watchingRun);
        if (gone) return;
        dispatch({ type: 'status', status, nowMs: clock.now() });
        // a finished run: read GET /demo again, for the next control and for the hero's replay
        if (FINAL_STATES.has(status.state)) refresh();
      } catch {
        // a missed poll: the next one follows
      }
      if (!gone) timer = setTimeout(poll, POLL_MS);
    };
    void poll();
    return () => {
      gone = true;
      clearTimeout(timer);
    };
  }, [watchingRun, demo, clock, refresh]);

  const send = async () => {
    dispatch({ type: 'send' });
    try {
      dispatch({ type: 'started', start: await demo.startStress() });
    } catch (error) {
      const e = error instanceof ApiError ? error : null;
      const retryAtMs =
        e?.code === 'DEMO_COOLING_DOWN' ? parseCooldownField(e.fields) : e?.retryAfterMs === undefined ? null : clock.now() + e.retryAfterMs;
      dispatch({ type: 'startFailed', code: e?.code ?? 'HTTP_0', reason: e?.fields?.reason, retryAtMs });
      if (e?.code === 'DEMO_COOLING_DOWN' && retryAtMs === null) refresh();
    }
  };

  const end = state.status?.end_time_ms ?? null;
  const leftMs = end === null ? null : Math.max(0, end - now);
  let action = null;
  if (control.kind === 'ready') {
    action = (
      <button type="button" className={styles.primaryButton} onClick={() => void send()}>
        {t.send}
      </button>
    );
  } else if (control.kind === 'sending' || control.kind === 'watching' || control.kind === 'cooling') {
    const label =
      control.kind === 'sending'
        ? t.sending
        : control.kind === 'cooling'
          ? t.nextRun(formatRemaining(Math.max(0, control.untilMs - now)))
          : phase(state) === 'settling'
            ? t.settlingButton
            : t.runningLeft(formatRemaining(leftMs ?? 0));
    action = (
      <button type="button" className={styles.primaryButton} disabled>
        {label}
      </button>
    );
  } else if (control.kind === 'unavailable') {
    const message =
      control.cause === 'rate_limited' && control.retryAtMs !== null
        ? t.unavailable.rateLimitedUntil(formatLocal(control.retryAtMs))
        : t.unavailable[control.cause];
    action = <p className={styles.unavailable}>{message}</p>;
  }

  return (
    <section className={styles.section} aria-labelledby="break-title">
      <h2 className={styles.sectionTitle} id="break-title">
        {t.title}
      </h2>
      <div className={styles.break}>
        <div>
          <p className={styles.intro}>{t.intro}</p>
          <div className={styles.actions}>{action}</div>
          {state.joined && control.kind === 'watching' ? <p className={styles.joined}>{t.joined}</p> : null}
        </div>
        <div>
          <Panel state={state} leftMs={leftMs} />
        </div>
        {/* the phase alone is announced; the counters change every second and are not */}
        <p className="sr-only" aria-live="polite">
          {t.announce[phase(state)]}
        </p>
      </div>
    </section>
  );
}
