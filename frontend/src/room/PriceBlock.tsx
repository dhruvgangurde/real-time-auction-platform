// The price region (brief 9.3): current bid, who leads and by which bid, the reserve as a fact
// only (BR-6), the outbid treatment once (brief 6), and every outcome stated once here. "You"
// comes from the server's you_lead and the viewer's own acks, never from an alias (item 15).
import { useState, type ReactNode } from 'react';

import { formatLocal } from '../lib/clock';
import { formatINR } from '../lib/money';
import { isStale, type RoomState } from '../live/room';
import { useStrings, type HelpTopic } from '../strings/catalogue';
import styles from './room.module.css';
import { changedFrom } from './time';

export function Check() {
  return (
    <svg className={styles.ic} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" aria-hidden="true">
      <path d="M4 12.5l5 5L20 6.5" />
    </svg>
  );
}

const inr = (n: number | null) => (n === null ? '' : formatINR(n));

export function PriceBlock({
  state,
  startTimeMs,
  onHelp,
}: {
  state: RoomState;
  startTimeMs: number | null;
  onHelp: (topic: HelpTopic) => void;
}) {
  const s = useStrings();
  const r = s.roomUi.price;
  const time = (ms: number | null) => (ms === null ? '' : formatLocal(ms, { seconds: true }));
  const won = state.own.result === 'won';
  const lost = state.terminal === 'closed_sold' && !won;
  const outbid = state.own.outbidAt !== null && state.terminal === null;
  const byMax = state.recentBids[0]?.bid_type === 'automatic';
  const amount = state.terminal === 'closed_sold' ? (state.finalPrice ?? state.price) : state.price;
  const shown = amount ?? state.startPrice;
  const text = shown === null ? '' : formatINR(shown);

  // the tick (brief 6): only the digits that changed, once per bid
  const [prevText, setPrevText] = useState(text);
  const [tick, setTick] = useState<{ from: number; key: number } | null>(null);
  if (prevText !== text) {
    setPrevText(text);
    setTick(prevText === '' ? null : { from: changedFrom(prevText, text), key: state.sequence });
  }
  // the bid that met the reserve, while this room stays open
  const [prevMet, setPrevMet] = useState(state.reserveMet);
  const [metAt, setMetAt] = useState<number | null>(null);
  if (prevMet !== state.reserveMet) {
    setPrevMet(state.reserveMet);
    setMetAt(prevMet === false && state.reserveMet === true ? state.sequence : null);
  }

  const seq = state.sequence;
  const ext = state.extensionCount;
  let box = styles.priceBox;
  let label: ReactNode = r.current;
  let line: ReactNode;
  if (won) {
    box = styles.priceBoxWon;
    label = (
      <span className={styles.youLead}>
        <Check />
        {r.wonAt}
      </span>
    );
    line = r.wonLine(seq, time(state.endTimeMs), ext);
  } else if (lost) {
    label = r.soldFor;
    const mine = state.recentBids.filter((b) => state.own.sequences.includes(b.sequence_no)).map((b) => b.amount);
    line = r.lostLine(seq, time(state.endTimeMs), ext, mine.length > 0 ? formatINR(Math.max(...mine)) : null);
  } else if (state.terminal === 'closed_no_sale') {
    label = amount === null ? r.starting : r.highest;
    line = r.noSaleLine(time(state.endTimeMs), seq);
  } else if (state.terminal === 'cancelled') {
    label = amount === null ? r.starting : r.lastBid;
    line = r.cancelledLine(seq);
  } else if (state.status === 'scheduled') {
    label = r.starting;
    line = r.opens(time(startTimeMs));
  } else if (outbid) {
    box = styles.priceBoxOut;
    label = r.outbid;
    const spent = state.own.maximum !== null && !state.own.maximumActive;
    line = spent
      ? r.maxReachedLine(inr(state.own.maximum), inr(state.own.outbidAt), inr(state.minNext))
      : r.outbidLine(inr(state.own.outbidAt), inr(state.minNext));
  } else if (state.status === 'closing') {
    line = state.own.leading ? r.youLeadWaiting(seq) : r.waiting;
  } else if (amount === null) {
    label = r.starting;
    line = r.noBids(inr(state.startPrice));
  } else if (state.own.leading) {
    line = (
      <span className={styles.youLead}>
        <Check />
        {r.youLead(seq, byMax)}
      </span>
    );
  } else {
    line = [r.leads(state.leaderAlias ?? '', seq, byMax), r.nextAtLeast(inr(state.minNext))].join(' ');
  }

  let reserve: ReactNode = null;
  const ask = (
    <button type="button" className={styles.link} onClick={() => onHelp('reserve')}>
      {s.help.topics.reserve.question}
    </button>
  );
  if (state.reserveMet !== null && state.terminal === null) {
    if (state.status === 'scheduled') {
      reserve = (
        <div className={styles.reserve}>
          {r.hasReserve} {ask}
        </div>
      );
    } else if (state.reserveMet) {
      reserve = (
        <div className={styles.reserveMet}>
          <Check />
          {metAt !== null ? r.reserveMetWith(metAt) : r.reserveMet}
        </div>
      );
    } else {
      reserve = (
        <div className={styles.reserve}>
          {r.reserveNotMet} {ask}
        </div>
      );
    }
  }

  const priceClass = `${styles.price} tabular${isStale(state) ? ` ${styles.stale}` : ''}`;
  return (
    <div className={box}>
      <div className={styles.plabel}>{label}</div>
      <p className={priceClass}>
        {tick === null || outbid ? (
          text
        ) : (
          <>
            {text.slice(0, tick.from)}
            <span key={tick.key} className={styles.tick}>
              {text.slice(tick.from)}
            </span>
          </>
        )}
      </p>
      <div className={styles.leadLine}>{line}</div>
      {reserve}
    </div>
  );
}
