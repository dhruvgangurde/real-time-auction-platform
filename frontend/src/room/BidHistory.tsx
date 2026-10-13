// The full bid history (FR-27), /auctions/:id/bids: newest first, paged with "Show more",
// pseudonyms and times in the viewer's zone. A regular viewer sees aliases only (BR-10); an
// admin also gets bidder ids, so their own rows are marked "You". A degraded note appears when
// the most recent bids may be missing.
import { useInfiniteQuery, useQuery } from '@tanstack/react-query';
import { Link, useParams } from 'react-router';

import { useApp, useToken } from '../app/context';
import { formatDay, formatLocal } from '../lib/clock';
import { formatINR } from '../lib/money';
import { useStrings } from '../strings/catalogue';
import styles from './room.module.css';

export function BidHistory() {
  const { id = '' } = useParams();
  const h = useStrings().history;
  const { api } = useApp();
  const token = useToken();
  const me = useQuery({ queryKey: ['me', token], queryFn: () => api.me(), enabled: token !== null, staleTime: 60_000 });
  const query = useInfiniteQuery({
    queryKey: ['history', id],
    queryFn: ({ pageParam }) => api.history(id, pageParam),
    initialPageParam: undefined as number | undefined,
    getNextPageParam: (last) => last.next_before ?? undefined,
  });

  const pages = query.data?.pages ?? [];
  const items = pages.flatMap((p) => p.items);
  const degraded = pages.some((p) => p.degraded);
  const decided = (ms: number | null) => (ms === null ? '' : `${formatDay(ms)}, ${formatLocal(ms, { seconds: true })}`);

  return (
    <section className={styles.historyPage}>
      <p>
        <Link className={styles.link} to={`/auctions/${id}`}>
          {h.toRoom}
        </Link>
      </p>
      <h1 className={styles.historyTitle}>{h.title}</h1>
      {degraded ? (
        <p className={styles.hint} role="status">
          {h.degraded}
        </p>
      ) : null}
      {query.isError ? (
        <p className={styles.hint} role="alert">
          {h.error}
        </p>
      ) : query.isPending ? (
        <p className={styles.hint}>{h.loading}</p>
      ) : items.length === 0 ? (
        <p className={styles.hint}>{h.empty}</p>
      ) : (
        <table className={styles.ledger}>
          <thead>
            <tr>
              <th className={styles.seq}>{h.no}</th>
              <th>{h.bidder}</th>
              <th className={styles.amt}>{h.amount}</th>
              <th className={styles.t}>{h.decidedAt}</th>
            </tr>
          </thead>
          <tbody>
            {items.map((b) => {
              const mine = b.bidder_id !== undefined && me.data !== undefined && b.bidder_id === me.data.id;
              return (
                <tr key={b.sequence_no}>
                  <td className={styles.seq}>{b.sequence_no}</td>
                  <td>
                    {mine ? <span className={styles.you}>{h.you}</span> : null}
                    <span className={mine ? styles.k : undefined}>{b.bidder_alias}</span>
                    {b.bid_type === 'automatic' ? <span className={styles.k}>{h.byMaximum}</span> : null}
                  </td>
                  <td className={styles.amt}>{formatINR(b.amount)}</td>
                  <td className={styles.t}>{decided(b.placed_at_ms)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
      {query.hasNextPage ? (
        <div className={styles.actions}>
          <button type="button" className={styles.btnQuiet} onClick={() => void query.fetchNextPage()} disabled={query.isFetchingNextPage}>
            {query.isFetchingNextPage ? h.loading : h.showMore}
          </button>
        </div>
      ) : null}
    </section>
  );
}

export const Component = BidHistory;
