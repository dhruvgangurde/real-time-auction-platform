// A listing card (brief 9.2): photo, title linking to the room, the status as a word and a mark
// (never colour alone), the price with its label, and the time line. Shared with the watchlist.
import type { ReactNode } from 'react';
import { Link } from 'react-router';

import { formatDay, formatLocal, formatRemaining } from '../lib/clock';
import type { AuctionCard } from '../lib/contract';
import { formatINR } from '../lib/money';
import { useStrings } from '../strings/catalogue';
import { cardView } from './cards';
import styles from './discover.module.css';

// the title beside the photo names the lot, so the photo is decorative (empty alt)
const DECORATIVE = '';
const DAY_MS = 86_400_000;

export function Card({ card, nowMs, action }: { card: AuctionCard; nowMs: number; action?: ReactNode }) {
  const c = useStrings().cards;
  const view = cardView(card, nowMs);
  const at = (ms: number) => `${formatDay(ms)}, ${formatLocal(ms)}`;
  let label: string | null = null;
  let price: number | null = null;
  let line: string;
  switch (view.kind) {
    case 'scheduled':
      [label, price, line] = [c.startingBid, view.price, c.startsAt(at(view.startsAtMs))];
      break;
    case 'live':
      [label, price] = [view.hasBids ? c.currentBid : c.startingBid, view.price];
      line = view.remainingMs < DAY_MS ? c.endsIn(formatRemaining(view.remainingMs)) : c.endsOn(at(view.endTimeMs));
      break;
    case 'closing':
      [label, price, line] = [view.hasBids ? c.currentBid : c.startingBid, view.price, c.closingNote];
      break;
    case 'sold':
      [label, price, line] = [c.soldFor, view.price, ''];
      break;
    case 'noSale':
      line = c.noSaleNote;
      break;
    case 'cancelled':
      line = c.cancelledNote;
      break;
  }
  const bids = view.kind === 'live' || view.kind === 'closing' ? c.bids(view.bids) : '';
  return (
    <article className={styles.card} data-kind={view.kind}>
      <div className={styles.media}>{card.thumbnail_url === null ? null : <img src={card.thumbnail_url} alt={DECORATIVE} loading="lazy" />}</div>
      <div className={styles.body}>
        <p className={styles.state}>
          <span className={styles.mark} aria-hidden="true" />
          {c.status[view.kind]}
        </p>
        <h2 className={styles.cardTitle}>
          <Link className={styles.cardLink} to={`/auctions/${card.id}`} data-card>
            {card.title}
          </Link>
        </h2>
        {label === null || price === null ? null : (
          <p className={styles.price}>
            <span className={styles.priceLabel}>{label}</span>
            <span className={`${styles.amount} tabular`}>{formatINR(price)}</span>
          </p>
        )}
        <p className={`${styles.meta} tabular`}>
          {line === '' ? null : <span>{line}</span>}
          {bids === '' ? null : <span>{bids}</span>}
        </p>
        {action === undefined ? null : <div className={styles.action}>{action}</div>}
      </div>
    </article>
  );
}
