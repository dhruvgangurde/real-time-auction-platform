// Route guards (UI-2). RequireAuth sends an anonymous viewer to sign in and brings them back to
// the same URL; RequireRole shows access denied in place, so there is never a redirect loop.
// Neither decides before the load-time restore has settled (brief 13 item 22).
import { useQuery } from '@tanstack/react-query';
import { Link, Navigate, Outlet, useLocation } from 'react-router';

import { useStrings } from '../strings/catalogue';
import { useApp, useAuthReady, useToken } from './context';
import styles from './Home.module.css';

export function RequireAuth() {
  const ready = useAuthReady();
  const token = useToken();
  const location = useLocation();
  if (!ready) return null;
  if (token === null) {
    return <Navigate to="/sign-in" replace state={{ from: location.pathname + location.search }} />;
  }
  return <Outlet />;
}

export function RequireRole({ role }: { role: 'seller' | 'admin' }) {
  const s = useStrings();
  const { api } = useApp();
  const ready = useAuthReady();
  const token = useToken();
  const location = useLocation();
  const me = useQuery({ queryKey: ['me', token], queryFn: () => api.me(), enabled: token !== null, staleTime: 60_000 });
  if (!ready) return null;
  if (token === null) {
    return <Navigate to="/sign-in" replace state={{ from: location.pathname + location.search }} />;
  }
  if (me.data === undefined) return null;
  if (!me.data.roles.includes(role)) {
    return (
      <section>
        <h1 className={styles.title}>{s.guards.deniedTitle}</h1>
        <p className={styles.lede}>{s.guards.denied(role)}</p>
        <p>
          <Link to="/auctions">{s.common.back}</Link>
        </p>
      </section>
    );
  }
  return <Outlet />;
}

/** A screen of a later slice: says so instead of rendering nothing. */
export function Placeholder() {
  const s = useStrings();
  return (
    <section>
      <h1 className={styles.title}>{s.placeholder.title}</h1>
      <p className={styles.lede}>{s.placeholder.body}</p>
    </section>
  );
}
