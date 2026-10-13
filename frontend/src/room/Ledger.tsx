// The bid feed (brief 9.3): the latest bids newest first; "You" labels a row placed by the
// viewer, matched by its own ack sequence or by your_alias (a label only, never leading, item
// 15), so rows the viewer's maximum placed are labelled too; "by maximum" without any amount of a
// maximum (BR-7); the pending row without a sequence number (UI-5); the way to the full history.
import type { CSSProperties } from 'react';
import { Link } from 'react-router';

import { formatDecided } from '../lib/clock';
import { formatINR } from '../lib/money';
import { isStale, type RoomState } from '../live/room';
import { useStrings } from '../strings/catalogue';
import styles from './room.module.css';

const SHOWN = 8;
const ANIMATED = 5; // brief 6: at most five rows animate

export function Ledger({ state }: { state: RoomState }) {
  const l = useStrings().roomUi.ledger;
  const own = new Set(state.own.sequences);
  const pending = state.pending?.kind === 'bid' ? state.pending : null;
  const checking = state.last?.kind === 'rejected' && state.last.code === 'SERVICE_UNAVAILABLE';
  const rows = state.recentBids.slice(0, SHOWN);
  // the marigold highlight is a live moment only: never while stale, degraded or terminal (brief 6)
  const highlight = !isStale(state) && state.terminal === null;
  const table = isStale(state) ? `${styles.ledger} ${styles.stale}` : styles.ledger;
  return (
    <div className={styles.ledgerWrap}>
      <h2 className={styles.ledgerHead}>
        <span>{l.title}</span>
        <Link className={styles.link} to={`/auctions/${state.auctionId}/bids`}>
          {l.history}
        </Link>
      </h2>
      <table className={table}>
        <thead>
          <tr>
            <th className={styles.seq}>{l.no}</th>
            <th>{l.bidder}</th>
            <th className={styles.amt}>{l.amount}</th>
            <th className={styles.t}>{l.decidedAt}</th>
          </tr>
        </thead>
        <tbody>
          {pending === null ? null : (
            <tr className={styles.pending}>
              <td className={styles.seq}>{l.pendingSeq}</td>
              <td className={styles.you}>{l.you}</td>
              <td className={styles.amt}>{formatINR(pending.amount)}</td>
              <td className={styles.t}>{checking ? l.checking : l.sending}</td>
            </tr>
          )}
          {rows.map((b, i) => {
            const mine = own.has(b.sequence_no) || (state.yourAlias !== null && b.bidder_alias === state.yourAlias);
            const newest = highlight && i === 0 && b.sequence_no === state.sequence && pending === null;
            const cls = i >= ANIMATED ? styles.rowStill : newest ? styles.rowNew : styles.row;
            return (
              <tr key={b.sequence_no} className={cls} style={{ '--i': i } as CSSProperties}>
                <td className={styles.seq}>{b.sequence_no}</td>
                <td>
                  {mine ? <span className={styles.you}>{l.you}</span> : null}
                  <span className={mine ? styles.k : undefined}>{b.bidder_alias}</span>
                  {b.bid_type === 'automatic' ? <span className={styles.k}>{l.byMaximum}</span> : null}
                </td>
                <td className={styles.amt}>{formatINR(b.amount)}</td>
                <td className={styles.t}>{b.placed_at_ms === null ? '' : formatDecided(b.placed_at_ms)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {rows.length === 0 && pending === null ? <p className={styles.hint}>{l.none}</p> : null}
    </div>
  );
}
