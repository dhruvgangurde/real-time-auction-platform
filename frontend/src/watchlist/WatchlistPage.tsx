// The watchlist, /watchlist (brief 9.7; FR-24, FR-25): the viewer's watched auctions in every
// status, ending soonest first, each with its current price and time left. REST only (never one
// subscription per item, brief 13 item 4): refetched every 15 s while the tab is visible, with
// one server-clock tick repainting every countdown. A removed item stays until the viewer leaves,
// with "Watch again".
import { useState } from 'react';
import { Link } from 'react-router';

import { ApiError } from '../api/client';
import { useApp } from '../app/context';
import { Card } from '../discover/Card';
import { POLL_MS } from '../discover/cards';
import styles from '../discover/discover.module.css';
import { useNow } from '../discover/useNow';
import type { AuctionCard, Watchlist } from '../lib/contract';
import { errorMessage, useStrings } from '../strings/catalogue';
import { WatchButton, useWatchlist } from '../watch/WatchButton';
import { kept } from './kept';

const SKELETONS = [0, 1, 2];

export function WatchlistPage() {
  const s = useStrings();
  const w = s.watchlist;
  const { clock } = useApp();
  const { list } = useWatchlist(POLL_MS); // the same cache entry as every Watch toggle, polled here
  const now = useNow(clock);
  // merged as each new list arrives (React's adjust-state-while-rendering pattern, no effect)
  const [shown, setShown] = useState<AuctionCard[]>([]);
  const [merged, setMerged] = useState<Watchlist | undefined>(undefined);
  if (list.data !== merged) {
    setMerged(list.data);
    if (list.data !== undefined) setShown(kept(shown, list.data.items));
  }
  const watched = new Set(list.data?.items.map((c) => c.id) ?? []);

  let body;
  if (list.isError && list.data === undefined) {
    const code = list.error instanceof ApiError ? list.error.code : null;
    body = (
      <div className={styles.empty} role="alert">
        <p className={styles.emptyHead}>{code === null || code === 'INTERNAL_ERROR' ? w.error : errorMessage(s, code)}</p>
        <button type="button" className={styles.btnQuiet} onClick={() => void list.refetch()}>
          {s.common.tryAgain}
        </button>
      </div>
    );
  } else if (list.data === undefined) {
    body = (
      <>
        <p className="sr-only" role="status">
          {s.common.loading}
        </p>
        <ul className={styles.grid} aria-hidden="true">
          {SKELETONS.map((i) => (
            <li key={i} className={styles.skeleton}>
              <div className={styles.media} />
              <div className={styles.bar} />
              <div className={styles.barShort} />
            </li>
          ))}
        </ul>
      </>
    );
  } else if (shown.length === 0) {
    body = (
      <div className={styles.empty}>
        <p className={styles.emptyHead}>{w.empty}</p>
        <Link className={styles.btnQuiet} to="/auctions">
          {w.find}
        </Link>
      </div>
    );
  } else {
    body = (
      <ul className={styles.grid}>
        {shown.map((c) => (
          <li key={c.id}>
            <Card card={c} nowMs={now} action={<WatchButton card={c} again={!watched.has(c.id)} />} />
          </li>
        ))}
      </ul>
    );
  }

  return (
    <section className={styles.page}>
      <h1 className={styles.title}>{w.title}</h1>
      <p className={styles.lede}>{w.lede}</p>
      <div className={styles.listTop}>{body}</div>
    </section>
  );
}

export const Component = WatchlistPage;
