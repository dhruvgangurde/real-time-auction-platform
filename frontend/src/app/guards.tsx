// Route guards (UI-2). RequireAuth sends an anonymous viewer to sign in and brings them back to
// the same URL; RequireRole shows access denied in place, so there is never a redirect loop.
// Neither decides before the load-time restore has settled (brief 13 item 22), and neither signs
// anyone out because the server could not be reached: that offers a retry instead (brief 9.1).
import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { Link, Navigate, Outlet, useLocation } from 'react-router';

import { useStrings } from '../strings/catalogue';
import { useApp, useAuthReady, useAuthUnreachable, useToken } from './context';
import styles from './Home.module.css';

/** No token: the server said there is no session (204, 401), or it could not be asked. */
function SignedOut() {
  const location = useLocation();
  if (useAuthUnreachable()) return <Unreachable />;
  return <Navigate to="/sign-in" replace state={{ from: location.pathname + location.search }} />;
}

/** The load-time refresh hit a network error or a 5xx: the session may still be alive. */
function Unreachable() {
  const s = useStrings();
  const { auth } = useApp();
  const [trying, setTrying] = useState(false);
  const retry = () => {
    if (trying) return;
    setTrying(true);
    void auth.restoreOnLoad().finally(() => setTrying(false));
  };
  return (
    <section>
      <h1 className={styles.title}>{s.guards.unreachableTitle}</h1>
      <p className={styles.lede}>{s.guards.unreachable}</p>
      <button type="button" className={styles.button} onClick={retry} aria-disabled={trying}>
        {trying ? s.common.trying : s.common.tryAgain}
      </button>
    </section>
  );
}

export function RequireAuth() {
  const ready = useAuthReady();
  const token = useToken();
  if (!ready) return null;
  if (token === null) return <SignedOut />;
  return <Outlet />;
}

export function RequireRole({ role }: { role: 'seller' | 'admin' }) {
  const s = useStrings();
  const { api } = useApp();
  const ready = useAuthReady();
  const token = useToken();
  const me = useQuery({ queryKey: ['me', token], queryFn: () => api.me(), enabled: token !== null, staleTime: 60_000 });
  if (!ready) return null;
  if (token === null) return <SignedOut />;
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
