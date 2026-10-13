// The app shell: skip link, header, main landmark and the two live regions (brief 9.1, 11.4).
// Every string comes from the catalogue; every colour from the tokens.
import { useQuery } from '@tanstack/react-query';
import { lazy, Suspense } from 'react';
import { Link, NavLink, Outlet } from 'react-router';

import { useStrings } from '../strings/catalogue';
import { useApp, useToken } from './context';
import styles from './Shell.module.css';

const SignOutButton = lazy(() => import('../auth/screens').then((m) => ({ default: m.SignOutButton })));

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
                <NavLink className={() => styles.deskOnly} to="/notifications">
                  {s.nav.notifications}
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
      <div className="sr-only" aria-live="polite" id="live-polite" />
      <div className="sr-only" aria-live="assertive" id="live-assertive" />
    </>
  );
}
