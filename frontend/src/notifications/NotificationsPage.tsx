// Notifications, /notifications (brief 9.6; FR-70, FR-74): newest first, all or unread only (the
// filter is in the URL), paged with "Show more", one label and sentence per type (rows.ts). Mark
// one or all read, optimistically (the list and the bell together), rolled back on a failure.
// Opening a row's link marks it read. The list refreshes when the bell sees a new arrival.
import { useInfiniteQuery, useMutation, useQueryClient, type InfiniteData } from '@tanstack/react-query';
import { Link, useSearchParams } from 'react-router';

import { ApiError } from '../api/client';
import { useApp, useViewerId } from '../app/context';
import { useUnreadCount } from '../app/useUnread';
import { formatDay, formatLocal } from '../lib/clock';
import type { NotificationPage } from '../lib/contract';
import { errorMessage, useStrings } from '../strings/catalogue';
import { EmailPreferences } from './EmailPreferences';
import styles from './notifications.module.css';
import { markReadIn, rowText } from './rows';

type Pages = InfiniteData<NotificationPage, number | undefined>;
const SKELETONS = [0, 1, 2, 3];

export function NotificationsPage() {
  const s = useStrings();
  const p = s.notificationsPage;
  const { api } = useApp();
  const client = useQueryClient();
  const viewer = useViewerId();
  const [search] = useSearchParams();
  const unreadOnly = search.get('filter') === 'unread';
  const unread = useUnreadCount();
  const query = useInfiniteQuery({
    queryKey: ['notifications', 'list', viewer, unreadOnly],
    queryFn: ({ pageParam }) => api.notifications(pageParam === undefined ? { unread: unreadOnly } : { unread: unreadOnly, before: pageParam }),
    initialPageParam: undefined as number | undefined,
    getNextPageParam: (last) => last.next_before ?? undefined,
    refetchOnWindowFocus: true,
  });

  const unreadKey = ['notifications', 'unread', viewer];
  const mark = useMutation({
    mutationFn: async (id: number | 'all') => {
      if (id === 'all') await api.markAllRead();
      else await api.markRead(id);
    },
    onMutate: async (id: number | 'all') => {
      await client.cancelQueries({ queryKey: ['notifications'] });
      const lists = client.getQueriesData<Pages>({ queryKey: ['notifications', 'list', viewer] });
      const count = client.getQueryData<NotificationPage>(unreadKey);
      for (const [key, data] of lists) if (data !== undefined) client.setQueryData<Pages>(key, markReadIn(data, id));
      if (count !== undefined) {
        client.setQueryData<NotificationPage>(unreadKey, { ...count, items: id === 'all' ? [] : count.items.filter((i) => i.id !== id) });
      }
      return { lists, count };
    },
    onError: (_error, _id, context) => {
      for (const [key, data] of context?.lists ?? []) client.setQueryData(key, data);
      if (context?.count !== undefined) client.setQueryData(unreadKey, context.count);
    },
    onSettled: () => client.invalidateQueries({ queryKey: ['notifications'] }),
  });

  const items = query.data?.pages.flatMap((page) => page.items) ?? [];
  const at = (ms: number) => `${formatDay(ms)}, ${formatLocal(ms)}`;

  let body;
  if (query.isError && query.data === undefined) {
    const code = query.error instanceof ApiError ? query.error.code : null;
    body = (
      <div className={styles.empty} role="alert">
        <p className={styles.emptyHead}>{code === null || code === 'INTERNAL_ERROR' ? p.error : errorMessage(s, code)}</p>
        <button type="button" className={styles.btnQuiet} onClick={() => void query.refetch()}>
          {s.common.tryAgain}
        </button>
      </div>
    );
  } else if (query.data === undefined) {
    body = (
      <>
        <p className="sr-only" role="status">
          {s.common.loading}
        </p>
        <ol className={styles.list} aria-hidden="true">
          {SKELETONS.map((i) => (
            <li key={i} className={styles.skeleton}>
              <div className={styles.barShort} />
              <div className={styles.bar} />
            </li>
          ))}
        </ol>
      </>
    );
  } else if (items.length === 0) {
    body = (
      <div className={styles.empty}>
        <p className={styles.emptyHead}>{unreadOnly ? p.caughtUp : p.empty}</p>
        {unreadOnly ? (
          <Link className={styles.btnQuiet} to="/notifications">
            {p.showAll}
          </Link>
        ) : (
          <p className={styles.hint}>{p.emptyNote}</p>
        )}
      </div>
    );
  } else {
    body = (
      <>
        <ol className={styles.list}>
          {items.map((n) => {
            const row = rowText(n, s);
            const open = () => {
              if (!n.read) mark.mutate(n.id);
            };
            return (
              <li key={n.id} className={n.read ? styles.row : styles.rowUnread}>
                <span className={styles.dot} aria-hidden="true" />
                <div className={styles.main}>
                  <p className={styles.label}>
                    {n.read ? null : <span className="sr-only">{p.unreadMark}</span>}
                    {row.label}
                  </p>
                  {row.href === null ? (
                    <p className={styles.text}>{row.text}</p>
                  ) : (
                    <Link className={styles.text} to={row.href} onClick={open}>
                      {row.text}
                    </Link>
                  )}
                  <p className={`${styles.time} tabular`}>
                    <time dateTime={new Date(n.created_at_ms).toISOString()}>{at(n.created_at_ms)}</time>
                  </p>
                </div>
                {n.read ? null : (
                  <button type="button" className={styles.markOne} onClick={() => mark.mutate(n.id)} aria-label={p.markReadLabel(row.text)}>
                    {p.markRead}
                  </button>
                )}
              </li>
            );
          })}
        </ol>
        {query.hasNextPage ? (
          <div className={styles.more}>
            <button
              type="button"
              className={styles.btnQuiet}
              onClick={() => void query.fetchNextPage()}
              aria-disabled={query.isFetchingNextPage}
            >
              {query.isFetchingNextPage ? p.loadingMore : p.showMore}
            </button>
          </div>
        ) : null}
      </>
    );
  }

  return (
    <section className={styles.page}>
      <h1 className={styles.title}>{p.title}</h1>
      <div className={styles.bar1}>
        <nav className={styles.filter} aria-label={p.filterLabel}>
          <Link to="/notifications" aria-current={unreadOnly ? undefined : 'page'}>
            {p.all}
          </Link>
          <Link to="/notifications?filter=unread" aria-current={unreadOnly ? 'page' : undefined}>
            {p.unread}
          </Link>
        </nav>
        <button
          type="button"
          className={styles.btnQuiet}
          onClick={() => {
            if (unread.count > 0 && !mark.isPending) mark.mutate('all');
          }}
          aria-disabled={unread.count === 0 || mark.isPending}
        >
          {p.markAll}
        </button>
      </div>
      <p className={styles.failed} role="status">
        {mark.isError ? p.failed : ''}
      </p>
      {body}
      <EmailPreferences />
    </section>
  );
}

export const Component = NotificationsPage;
