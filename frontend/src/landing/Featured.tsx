// Brief 8.5: five to seven real listings, live and upcoming, as an editorial spread: one large plate
// and several smaller ones, captions set like a catalogue. Demo lots are never listed (the server
// leaves them out of GET /auctions), so none appears here. What each caption says comes from
// discover/cards.ts, as on Discover; the title is the one link to the room.
import { useEffect, useState } from 'react';
import { Link } from 'react-router';

import { useApp } from '../app/context';
import { cardView } from '../discover/cards';
import { useNow } from '../discover/useNow';
import { formatDay, formatLocal, formatRemaining } from '../lib/clock';
import type { AuctionCard } from '../lib/contract';
import { formatINR } from '../lib/money';
import { useStrings } from '../strings/catalogue';
import { landingEnIN } from '../strings/landing-en-IN';
import type { LandingApi } from './api';
import styles from './landing.module.css';

const t = landingEnIN.featured;
// the title under the photo names the lot, so the photo is decorative (empty alt), as on Discover
const DECORATIVE = '';
const DAY_MS = 86_400_000;

type Lots = { kind: 'loading' } | { kind: 'done'; lots: AuctionCard[] } | { kind: 'failed' };

function Caption({ lot, nowMs }: { lot: AuctionCard; nowMs: number }) {
  const c = useStrings().cards;
  const view = cardView(lot, nowMs);
  const at = (ms: number) => `${formatDay(ms)}, ${formatLocal(ms)}`;
  let label = c.startingBid;
  let line = '';
  if (view.kind === 'scheduled' || view.kind === 'draft') line = c.startsAt(at(view.startsAtMs));
  if (view.kind === 'live') {
    label = view.hasBids ? c.currentBid : c.startingBid;
    line = view.remainingMs < DAY_MS ? c.endsIn(formatRemaining(view.remainingMs)) : c.endsOn(at(view.endTimeMs));
  }
  if (view.kind === 'closing') [label, line] = [view.hasBids ? c.currentBid : c.startingBid, c.closingNote];
  const price = 'price' in view ? view.price : null;
  const bids = view.kind === 'live' || view.kind === 'closing' ? c.bids(view.bids) : '';
  return (
    <figcaption className={styles.caption}>
      <h3 className={styles.lotTitle}>
        <Link to={`/auctions/${lot.id}`}>{lot.title}</Link>
      </h3>
      {price === null ? null : (
        <p className={`${styles.lotPrice} tabular`}>
          <b>{formatINR(price)}</b> <span>{label}</span>
        </p>
      )}
      <p className={`${styles.lotMeta} tabular`}>
        {line === '' ? null : <span>{line}</span>}
        {bids === '' ? null : <span>{bids}</span>}
      </p>
    </figcaption>
  );
}

export function Featured({ demo }: { demo: LandingApi }) {
  const { clock } = useApp();
  const now = useNow(clock);
  const [lots, setLots] = useState<Lots>({ kind: 'loading' });
  useEffect(() => {
    let gone = false;
    demo.featured().then(
      (items) => {
        if (!gone) setLots({ kind: 'done', lots: items });
      },
      () => {
        if (!gone) setLots({ kind: 'failed' });
      },
    );
    return () => {
      gone = true;
    };
  }, [demo]);

  return (
    <section className={`${styles.section} ${styles.band}`} aria-labelledby="lots-title">
      <p className={styles.eyebrow}>{t.eyebrow}</p>
      <h2 className={styles.sectionTitle} id="lots-title">
        {t.title}
      </h2>
      {lots.kind === 'loading' ? <div className={styles.spreadReserve} /> : null}
      {lots.kind === 'failed' || (lots.kind === 'done' && lots.lots.length === 0) ? (
        <p className={styles.noLots}>
          {lots.kind === 'failed' ? t.failed : t.none} <Link to="/auctions">{t.browse}</Link>
        </p>
      ) : null}
      {lots.kind === 'done' && lots.lots.length > 0 ? (
        <div className={styles.spread}>
          {lots.lots.map((lot, i) => (
            <figure key={lot.id} className={i === 0 ? styles.lotBig : styles.lot}>
              <div className={styles.lotPlate}>{lot.thumbnail_url === null ? null : <img src={lot.thumbnail_url} alt={DECORATIVE} loading="lazy" />}</div>
              <Caption lot={lot} nowMs={now} />
            </figure>
          ))}
        </div>
      ) : null}
    </section>
  );
}
