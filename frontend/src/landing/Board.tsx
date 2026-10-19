// The hero's card (design/polish): the replay of the most recent verified "Try to break it" run,
// from its own ledger, or the empty state when there is none. Every number on it is the run's: the
// price is only ever an accepted amount, the clock is the recorded end minus the replay time, and
// the rows appear at their Redis decision times (replay.ts). No photo: the h1 stays the LCP.
import { useRef } from 'react';
import { Link } from 'react-router';

import { formatDay, formatDecided, formatLocal, formatRemaining } from '../lib/clock';
import { formatINR } from '../lib/money';
import { changedFrom } from '../room/time';
import { landingEnIN } from '../strings/landing-en-IN';
import type { LandingApi } from './api';
import styles from './board.module.css';
import { cardWords } from './cardWords';
import { ANIMATED, frameAt, type ReplayBid } from './replay';
import { FRAME_MS, usePlayback, useRecording } from './useReplay';

const t = landingEnIN.board;
const BAND_MS = 1_200; // how long the newest row and the changed digits keep their marigold fill

export function EmptyBoard({ title = t.emptyTitle }: { title?: string }) {
  return (
    <div className={`${styles.card} ${styles.empty}`}>
      <h2 className={styles.emptyTitle}>{title}</h2>
      <p>{t.emptyBody}</p>
    </div>
  );
}

function Price({ amount, previous, fresh }: { amount: number; previous: number | null; fresh: boolean }) {
  const text = formatINR(amount);
  if (!fresh || previous === null) return <>{text}</>;
  const from = changedFrom(formatINR(previous), text);
  return (
    <>
      {text.slice(0, from)}
      <span key={amount} className={styles.tick}>
        {text.slice(from)}
      </span>
    </>
  );
}

function Rows({ rows, now }: { rows: ReplayBid[]; now: number }) {
  // only what a frame added animates: at most five, the newest with the band (brief 8.1)
  const fresh = Math.min(ANIMATED, rows.filter((b) => b.at > now - FRAME_MS).length);
  return (
    <table className={styles.ledger}>
      <caption className="sr-only">{t.ledger.caption}</caption>
      <thead className="sr-only">
        <tr>
          <th className={styles.seq} scope="col">
            {t.ledger.sequence}
          </th>
          <th scope="col">{t.ledger.bidder}</th>
          <th className={styles.amount} scope="col">
            {t.ledger.amount}
          </th>
          <th className={styles.time} scope="col">
            {t.ledger.decided}
          </th>
          <th className={styles.kind} scope="col">
            <span className="sr-only">{t.ledger.byMaximum}</span>
          </th>
        </tr>
      </thead>
      <tbody>
        {rows.map((b, i) => {
          const band = i === 0 && now - b.at < BAND_MS;
          const enter = i > 0 && i < fresh;
          return (
            <tr key={b.seq} className={band ? styles.rowNew : enter ? styles.rowEnter : undefined}>
              <td className={`${styles.seq} tabular`}>{b.seq}</td>
              <td>{b.alias}</td>
              <td className={`${styles.amount} tabular`}>{formatINR(b.amount)}</td>
              <td className={`${styles.time} tabular`}>{formatDecided(b.at)}</td>
              <td className={styles.kind}>{b.auto ? t.ledger.byMaximum : null}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

const CHECK = 'M20 6 9 17l-5-5';

const Tick = () => (
  <svg className={styles.check} viewBox="0 0 24 24" aria-hidden="true">
    <path d={CHECK} />
  </svg>
);

export function Board({ demo, auctionId }: { demo: LandingApi; auctionId: string }) {
  const recording = useRecording(demo, auctionId);
  const ref = useRef<HTMLDivElement>(null);
  const rec = recording.kind === 'ready' ? recording.rec : null;
  const play = usePlayback(rec, ref);

  if (recording.kind === 'failed') return <EmptyBoard />;
  const lot = recording.lot;
  const when = lot === null ? null : { day: formatDay(lot.start_time_ms), time: formatLocal(lot.start_time_ms) };

  const head = (
    <>
      <div className={styles.head}>
        <h2 className={styles.title}>{lot?.title}</h2>
        <span className={styles.tag}>{t.replay}</span>
      </div>
      {when ? <p className={styles.status}>{t.replayOf(when.day, when.time)}</p> : null}
    </>
  );

  let body = <p className={styles.loading}>{t.loading}</p>;
  if (rec !== null && lot !== null && when !== null) {
    const frame = frameAt(rec, play.t);
    const latest = frame.latest;
    const previous = frame.applied >= 2 ? rec.bids[frame.applied - 2]!.amount : null;
    const count = frame.applied + rec.earlier;
    const reserve = lot.has_reserve ? (lot.reserve_met ? t.reserveMet : t.reserveNotMet) : t.noReserve;
    const time = (ms: number) => formatLocal(ms, { seconds: true });
    const words = cardWords(latest, play.finished, lot.status === 'closed_sold', formatINR(lot.start_price));
    body = (
      <>
        <div className={styles.priceRow}>
          <div>
            <div className={styles.label}>{words.label}</div>
            <p className={`${styles.price} tabular`}>
              {latest === null ? formatINR(lot.start_price) : <Price amount={latest.amount} previous={previous} fresh={play.t - latest.at < BAND_MS} />}
            </p>
          </div>
          <span className={`${styles.clockTime} tabular`}>{formatRemaining(frame.remainingMs)}</span>
        </div>
        <p className={styles.lead}>
          {words.lead}{' '}
          {latest === null ? null : t.bids(count)} {reserve}{' '}
          {play.finished
            ? t.closedAt(time(rec.endMs))
            : t.ends(time(rec.endMs), lot.antisnipe_threshold_s, formatRemaining(lot.antisnipe_extension_s * 1000))}
          {lot.extension_count > 0 ? ' ' : null}
          {lot.extension_count > 0 ? t.extended(lot.extension_count) : null}
        </p>
        {/* how the newest bid landed: the strip carries its real sequence number */}
        {latest === null ? null : (
          <ol className={styles.steps}>
            <li>{t.steps.sent}</li>
            <li className={styles.key}>{t.steps.decides(latest.seq)}</li>
            <li>{t.steps.sees}</li>
          </ol>
        )}
        <Rows rows={frame.rows} now={play.t} />
        <div className={styles.foot}>
          <ul className={styles.verified}>
            {t.verified.map((v) => (
              <li key={v}>
                <Tick />
                {v}
              </li>
            ))}
          </ul>
          <Link className={styles.ledgerLink} to={`/auctions/${auctionId}/bids`}>
            {t.seeLedger}
          </Link>
        </div>
        {play.finished ? (
          <div className={styles.result}>
            <p>
              {lot.status === 'closed_sold' && lot.leader_alias !== null && lot.current_price !== null
                ? t.sold(lot.leader_alias, formatINR(lot.current_price), lot.sequence_no)
                : t.noSale}
            </p>
            <button type="button" className={styles.again} onClick={play.again}>
              {t.again}
            </button>
          </div>
        ) : null}
      </>
    );
  }

  return (
    <div ref={ref} className={styles.card} role="region" aria-label={t.label}>
      {head}
      {body}
    </div>
  );
}
