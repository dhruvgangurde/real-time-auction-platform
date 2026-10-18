// The hero's board (brief 8.1, design/mockups/landing.html): the replay of the most recent verified
// "Try to break it" run, from its own ledger, or the empty state when there is none. Every number
// on it is the run's: the price is only ever an accepted amount, the clock is the recorded end
// minus the replay time, and the rows appear at their Redis decision times (replay.ts).
import { useRef } from 'react';
import { Link } from 'react-router';

import { formatDay, formatDecided, formatLocal, formatRemaining } from '../lib/clock';
import { formatINR } from '../lib/money';
import { changedFrom } from '../room/time';
import { Timeline } from '../room/Timeline';
import { landingEnIN } from '../strings/landing-en-IN';
import type { LandingApi } from './api';
import styles from './board.module.css';
import { photoCredit } from './credit';
import { ANIMATED, frameAt, type ReplayBid } from './replay';
import { FRAME_MS, usePlayback, useRecording } from './useReplay';

const t = landingEnIN.board;
const BAND_MS = 1_200; // how long the newest row and the changed digits keep their marigold fill
const FINAL_EXTENSION = 10; // FR-54

export function EmptyBoard() {
  return (
    <div className={styles.empty}>
      <h2 className={styles.emptyTitle}>{t.emptyTitle}</h2>
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

function Rows({ rows, now, auctionId, earlier }: { rows: ReplayBid[]; now: number; auctionId: string; earlier: number | null }) {
  // only what a frame added animates: at most five, the newest with the band (brief 8.1)
  const fresh = Math.min(ANIMATED, rows.filter((b) => b.at > now - FRAME_MS).length);
  return (
    <table className={styles.ledger}>
      <caption className="sr-only">{t.ledger.caption}</caption>
      <thead>
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
        {earlier === null ? null : (
          <tr>
            <td className={styles.more} colSpan={5}>
              <Link to={`/auctions/${auctionId}/bids`}>{t.earlier(earlier)}</Link>
            </td>
          </tr>
        )}
      </tbody>
    </table>
  );
}

export function Board({ demo, auctionId }: { demo: LandingApi; auctionId: string }) {
  const recording = useRecording(demo, auctionId);
  const ref = useRef<HTMLDivElement>(null);
  const rec = recording.kind === 'ready' ? recording.rec : null;
  const play = usePlayback(rec, ref);

  if (recording.kind === 'failed') return <EmptyBoard />;
  const lot = recording.lot;
  const image = lot === null ? undefined : [...lot.images].sort((a, b) => a.position - b.position)[0];
  const credit = lot === null ? null : photoCredit(lot.description);
  const when = lot === null ? null : { day: formatDay(lot.start_time_ms), time: formatLocal(lot.start_time_ms) };

  const head = (
    <div className={styles.head}>
      {/* the phone's photo: the plate is hidden there, so this one carries the description */}
      {image ? <img className={styles.thumb} src={image.url} alt={lot?.title ?? ''} /> : null}
      <h2 className={styles.title}>{lot?.title}</h2>
      <span className={styles.tag}>{t.demoLot}</span>
      {when ? (
        <span className={styles.status}>
          <i className={styles.dot} aria-hidden="true" />
          {t.replayOf(when.day, when.time)}
        </span>
      ) : null}
    </div>
  );

  let body = <p className={styles.loading}>{t.loading}</p>;
  if (rec !== null && lot !== null && when !== null) {
    const frame = frameAt(rec, play.t);
    const latest = frame.latest;
    const previous = frame.applied >= 2 ? rec.bids[frame.applied - 2]!.amount : null;
    const count = frame.applied + rec.earlier;
    const reserve = lot.has_reserve ? (lot.reserve_met ? t.reserveMet : t.reserveNotMet) : t.noReserve;
    const time = (ms: number) => formatLocal(ms, { seconds: true });
    const capped = lot.extension_count >= FINAL_EXTENSION;
    body = (
      <>
        <p className={styles.replay}>{t.replayNote(when.day, formatRemaining(play.elapsedMs), formatRemaining(play.durationMs))}</p>
        <div className={styles.label}>{latest === null ? t.startingPrice : t.currentBid}</div>
        <p className={`${styles.price} tabular`}>
          {latest === null ? formatINR(lot.start_price) : <Price amount={latest.amount} previous={previous} fresh={play.t - latest.at < BAND_MS} />}
        </p>
        <p className={styles.lead}>
          {latest === null ? t.noBids(formatINR(lot.start_price)) : t.leads(latest.alias, latest.seq)}{' '}
          {latest === null ? null : t.bids(count)} {reserve}
        </p>
        <div className={styles.clock}>
          <span className={`${styles.clockTime} tabular`}>{formatRemaining(frame.remainingMs)}</span>
          <span className={styles.clockNote}>
            {play.finished
              ? t.closedAt(time(rec.endMs))
              : t.ends(time(rec.endMs), lot.antisnipe_threshold_s, formatRemaining(lot.antisnipe_extension_s * 1000))}
            {lot.extension_count > 0 ? ' ' : null}
            {lot.extension_count > 0 ? t.extended(lot.extension_count) : null}
          </span>
        </div>
        <Timeline
          nowMs={play.t}
          endMs={rec.endMs}
          oldEndMs={null}
          windowMs={lot.antisnipe_threshold_s * 1000}
          extensionMs={lot.antisnipe_extension_s * 1000}
          capped={capped || play.finished}
          sequence={0}
        />
        <Rows rows={frame.rows} now={play.t} auctionId={auctionId} earlier={frame.earlierRow} />
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
    <div ref={ref} className={styles.band} role="region" aria-label={t.label}>
      <figure className={styles.figure}>
        <div className={styles.plate}>{image ? <img src={image.url} alt={lot?.title ?? ''} /> : null}</div>
        {credit ? <figcaption className={styles.credit}>{credit}</figcaption> : null}
      </figure>
      <div>
        {head}
        {body}
      </div>
    </div>
  );
}
