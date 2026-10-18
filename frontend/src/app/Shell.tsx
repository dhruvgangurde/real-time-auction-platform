// The app shell: skip link, header, main landmark and the two live regions (brief 9.1, 11.4).
// Every string comes from the catalogue; every colour from the tokens.
import { useQueryClient } from '@tanstack/react-query';
import { lazy, Suspense, useEffect, useRef, useState } from 'react';
import { Link, NavLink, Outlet, ScrollRestoration } from 'react-router';

import { announcePolite } from '../lib/polite';
import { useStrings } from '../strings/catalogue';
import { useMe, useToken } from './context';
import styles from './Shell.module.css';
import { applyTheme, nextTheme, readTheme, storage, writeTheme, type Theme } from './theme';
import { arrived, badge } from './unread';
import { useUnreadCount } from './useUnread';

const SignOutButton = lazy(() => import('../auth/screens').then((m) => ({ default: m.SignOutButton })));

/** The unread count for the bell, announced politely when it rises; a rise also refreshes the
 * notifications list if it is open. */
function useUnread(): number {
  const s = useStrings();
  const client = useQueryClient();
  const { count, loaded, viewer } = useUnreadCount();
  const previous = useRef<{ viewer: string | null; count: number } | null>(null);
  useEffect(() => {
    if (!loaded) return;
    const before = previous.current?.viewer === viewer ? previous.current.count : undefined;
    if (arrived(before, count)) {
      announcePolite(s.notifications.arrived(count));
      void client.invalidateQueries({ queryKey: ['notifications', 'list'] });
    }
    previous.current = { viewer, count };
  }, [loaded, count, viewer, s, client]);
  return count;
}

// the control's icon for each theme: a screen (follow the device), the sun, the moon
const THEME_ICON: Record<Theme, string> = {
  system: 'M3 4.5h14v9H3zM7.5 16.5h5M10 13.5v3',
  light: 'M10 6.5a3.5 3.5 0 1 1 0 7 3.5 3.5 0 0 1 0-7ZM10 2v2M10 16v2M2 10h2M16 10h2M4.3 4.3l1.4 1.4M14.3 14.3l1.4 1.4M4.3 15.7l1.4-1.4M14.3 5.7l1.4-1.4',
  dark: 'M16 12.5A6.5 6.5 0 0 1 7.5 4a6.5 6.5 0 1 0 8.5 8.5Z',
};

/** System, Light or Dark: one button that steps through them, its name saying which is on and
 * which comes next; the choice is stored and applied to <html> at once. */
function ThemeToggle() {
  const s = useStrings();
  const [theme, setTheme] = useState<Theme>(() => readTheme(storage()));
  const next = nextTheme(theme);
  const label = s.nav.themeLabel(s.nav.themes[theme], s.nav.themes[next]);
  const change = () => {
    writeTheme(storage(), next);
    applyTheme(document.documentElement, next);
    setTheme(next);
  };
  return (
    <button type="button" className={styles.theme} onClick={change} aria-label={label} title={label}>
      <svg className={styles.themeIcon} viewBox="0 0 20 20" aria-hidden="true">
        <path d={THEME_ICON[theme]} />
      </svg>
      <span className={styles.deskOnly}>{s.nav.themes[theme]}</span>
    </button>
  );
}

export function Shell() {
  const s = useStrings();
  const token = useToken();
  const me = useMe();
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
                {/* an icon at every width, the word on desktop: at 390 the header has room for icons only */}
                <NavLink className={() => styles.bell} to="/watchlist" aria-label={s.nav.watchlist}>
                  <svg className={styles.heartIcon} viewBox="0 0 16 16" aria-hidden="true">
                    <path d="M8 13.5 2.6 8.3a3.2 3.2 0 0 1 4.5-4.6L8 4.6l.9-.9a3.2 3.2 0 0 1 4.5 4.6Z" />
                  </svg>
                  <span className={styles.deskOnly}>{s.nav.watchlist}</span>
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
                {me.data?.roles.includes('seller') ? (
                  <NavLink className={() => styles.bell} to="/sell" aria-label={s.nav.sell}>
                    <svg className={styles.heartIcon} viewBox="0 0 16 16" aria-hidden="true">
                      <path d="M2.5 2.5h5l6 6-5 5-6-6Z M5.2 5.2h.01" />
                    </svg>
                    <span className={styles.deskOnly}>{s.nav.sell}</span>
                  </NavLink>
                ) : null}
                {me.data?.roles.includes('admin') ? (
                  <NavLink className={() => styles.deskOnly} to="/admin">
                    {s.nav.admin}
                  </NavLink>
                ) : null}
                <NavLink className={() => styles.account} to="/account">
                  {me.data?.full_name ?? s.nav.account}
                </NavLink>
                <Suspense fallback={null}>
                  <SignOutButton className={`${styles.signOut} ${styles.deskOnly}`} />
                </Suspense>
              </>
            ) : (
              <>
                <NavLink className={() => styles.deskOnly} to="/help">
                  {s.nav.how}
                </NavLink>
                <NavLink to="/sign-in">{s.nav.signIn}</NavLink>
              </>
            )}
            <ThemeToggle />
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
