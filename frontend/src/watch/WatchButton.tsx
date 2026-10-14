// The Watch toggle (FR-24) on cards and in the room. Signed in: aria-pressed, optimistic, rolled
// back with a message on a refusal (watch.ts). Anonymous: sign in, then back to the same page;
// nothing is added on the way (the click was made signed out).
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useLocation, useNavigate } from 'react-router';

import { ApiError } from '../api/client';
import { useApp, useToken, useViewerId } from '../app/context';
import type { AuctionCard } from '../lib/contract';
import { useStrings } from '../strings/catalogue';
import styles from './watch.module.css';
import { watchOptions } from './watch';

/** The viewer's watchlist, one cache entry per user (not per token, which changes every refresh). */
export function useWatchlist(refetchInterval: number | false = false) {
  const { api } = useApp();
  const token = useToken();
  const key = ['watchlist', useViewerId()] as const;
  const list = useQuery({ queryKey: key, queryFn: () => api.watchlist(), enabled: token !== null, staleTime: 15_000, refetchInterval });
  return { key, list, token };
}

/** `again`: the watchlist page's label for an item removed during this visit. `visible`: whether
 * to show the toggle given the watched state; by default everything but an unwatched cancelled
 * auction (the server refuses it, ALREADY_CLOSED). */
export function WatchButton({
  card,
  note,
  again = false,
  visible = (watched) => watched || card.status !== 'cancelled',
}: {
  card: AuctionCard;
  note?: string;
  again?: boolean;
  visible?: (watched: boolean) => boolean;
}) {
  const w = useStrings().watch;
  const { api, auth } = useApp();
  const client = useQueryClient();
  const navigate = useNavigate();
  const location = useLocation();
  const { key, list, token } = useWatchlist();
  const mutation = useMutation(watchOptions(client, api, key));
  const watched = token !== null && (list.data?.items.some((c) => c.id === card.id) ?? false);
  if (!visible(watched)) return null;

  const onClick = async () => {
    // a click during the load-time restore waits for it: until then a signed-in viewer has no token
    // yet and would be sent to sign in by mistake
    if (!auth.isRestored()) await auth.ready;
    if (auth.getToken() === null) {
      void navigate('/sign-in', { state: { from: location.pathname + location.search } });
      return;
    }
    // before the list has loaded the toggle reads "Watch": a watch is idempotent on the server, and
    // the refetch after it shows the real state, so the click is sent rather than dropped
    if (mutation.isPending) return;
    mutation.mutate({ auctionId: card.id, watch: !watched, card });
  };
  const error = mutation.error;
  const message =
    error === null ? '' : error instanceof ApiError && error.code === 'ALREADY_CLOSED' ? w.cancelled : watched ? w.removeFailed : w.addFailed;
  return (
    <div className={styles.watch}>
      <button
        type="button"
        className={watched ? styles.on : styles.off}
        aria-pressed={token === null ? undefined : watched}
        aria-disabled={mutation.isPending}
        onClick={() => void onClick()}
      >
        <svg className={styles.icon} viewBox="0 0 16 16" aria-hidden="true">
          <path d="M8 13.5 2.6 8.3a3.2 3.2 0 0 1 4.5-4.6L8 4.6l.9-.9a3.2 3.2 0 0 1 4.5 4.6Z" />
        </svg>
        {watched ? w.watching : again ? w.again : w.watch}
      </button>
      <p className={styles.message} role="status">
        {message}
      </p>
      {note === undefined || message !== '' ? null : <p className={styles.note}>{note}</p>}
    </div>
  );
}
