// Route guards (UI-2). RequireAuth sends an anonymous viewer to sign in and brings them back to
// the same URL; RequireRole shows access denied in place, so there is never a redirect loop.
// Neither decides before the load-time restore has settled (brief 13 item 22), and neither signs
// anyone out because the server could not be reached: that offers a retry instead (brief 9.1).
import { useQuery } from '@tanstack/react-query';
import { useEffect } from 'react';
import { Link, Navigate, Outlet, useLocation, useParams } from 'react-router';

import { useStrings } from '../strings/catalogue';
import { useApp, useAuthReady, useAuthUnreachable, useToken } from './context';
import styles from './Home.module.css';
import { NotFound } from './NotFound';
import { Unreachable } from './Unreachable';

/** /auctions/:id/pay is the payment page, a document of its own (brief 13 item 25): an SPA link
 * that lands here loads it in full. nginx serves that document for a uuid only, so any other id
 * is not found here instead of reloading forever. */
export function PayDocument() {
  const { id = '' } = useParams();
  const isUuid = /^[0-9a-f-]{36}$/.test(id);
  useEffect(() => {
    if (isUuid) window.location.replace(window.location.href);
  }, [isUuid]);
  return isUuid ? null : <NotFound />;
}

/** No token: the server said there is no session (204, 401), or it could not be asked. */
function SignedOut() {
  const location = useLocation();
  if (useAuthUnreachable()) return <Unreachable />;
  return <Navigate to="/sign-in" replace state={{ from: location.pathname + location.search }} />;
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
