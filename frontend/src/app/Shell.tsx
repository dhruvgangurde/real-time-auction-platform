// The app shell: skip link, header, main landmark and the two live regions (brief 9.1, 11.4).
// Every string comes from the catalogue; every colour from the tokens.
import { useQuery } from '@tanstack/react-query';
import { lazy, Suspense, useEffect, useRef } from 'react';
import { Link, NavLink, Outlet, ScrollRestoration } from 'react-router';

import { announcePolite } from '../lib/polite';
import { useStrings } from '../strings/catalogue';
import { useApp, useToken, useViewerId } from './context';
import styles from './Shell.module.css';
import { arrived, badge, unreadCount, UNREAD_LIMIT, UNREAD_POLL_MS } from './unread';

const SignOutButton = lazy(() => import('../auth/screens').then((m) => ({ default: m.SignOutButton })));

/** The unread count for the bell (unread.ts), announced politely when it rises. */
function useUnread(): number {
  const s = useStrings();
  const { api } = useApp();
  const token = useToken();
  const viewer = useViewerId();
  const unread = useQuery({
    queryKey: ['notifications', 'unread', viewer],
    queryFn: () => api.notifications({ unread: true, limit: UNREAD_LIMIT }),
    enabled: token !== null,
    refetchInterval: UNREAD_POLL_MS, // paused while the tab is hidden
    refetchOnWindowFocus: true, // and read again when it is shown
  });
  const count = unread.data === undefined ? 0 : unreadCount(unread.data);
  const previous = useRef<{ viewer: string | null; count: number } | null>(null);
  useEffect(() => {
    if (unread.data === undefined) return;
    const before = previous.current?.viewer === viewer ? previous.current.count : undefined;
    if (arrived(before, count)) announcePolite(s.notifications.arrived(count));
    previous.current = { viewer, count };
  }, [unread.data, count, viewer, s]);
  return token === null ? 0 : count;
}

export function Shell() {
  const s = useStrings();
  const { api } = useApp();
  const token = useToken();
  const me = useQuery({
    queryKey: ['me', token],
    queryFn: () => api.me(),
    enabled: token !== null,
    staleTime: 60_000,
  });
  const signedIn = token !== null;
  const unread = useUnread();
  return (
    <>
      <a className={styles.skip} href="#main">
        {s.app.skipToContent}
      </a>
      <header className={styles.header}>
        <div className={styles.wrap}>
          <Link className={styles.mark} to="/">
            {s.app.name}
          </Link>
          <nav className={styles.nav} aria-label={s.app.mainNav}>
            <NavLink className={() => styles.deskOnly} to="/auctions">
              {s.nav.live}
            </NavLink>
            {signedIn ? (
              <>
                <NavLink className={() => styles.deskOnly} to="/watchlist">
                  {s.nav.watchlist}
                </NavLink>
                <NavLink className={() => styles.bell} to="/notifications" aria-label={s.nav.notificationsLabel(unread)}>
                  <svg className={styles.bellIcon} viewBox="0 0 20 20" aria-hidden="true">
                    <path d="M5 8.5a5 5 0 0 1 10 0c0 3.6 1.4 5 2 5.5H3c.6-.5 2-1.9 2-5.5ZM8 16.5a2 2 0 0 0 4 0" />
                  </svg>
                  <span className={styles.deskOnly}>{s.nav.notifications}</span>
                  {unread === 0 ? null : (
                    <span className={styles.badge} aria-hidden="true">
                      {badge(unread)}
                    </span>
                  )}
                </NavLink>
                <NavLink to="/account">{me.data?.full_name ?? s.nav.account}</NavLink>
                <Suspense fallback={null}>
                  <SignOutButton className={`${styles.signOut} ${styles.deskOnly}`} />
                </Suspense>
              </>
            ) : (
              <>
                <NavLink className={() => styles.deskOnly} to="/how-it-works">
                  {s.nav.how}
                </NavLink>
                <NavLink to="/sign-in">{s.nav.signIn}</NavLink>
              </>
            )}
          </nav>
        </div>
      </header>
      <main id="main" className={styles.main} tabIndex={-1} aria-label={s.app.main}>
        <Suspense fallback={<p className={styles.loading}>{s.common.loading}</p>}>
          <Outlet />
        </Suspense>
      </main>
      {/* back and forward return to the same scroll position (Discover's list, brief 9.2) */}
      <ScrollRestoration />
      <div className="sr-only" aria-live="polite" id="live-polite" />
      <div className="sr-only" aria-live="assertive" id="live-assertive" />
    </>
  );
}
