// An admin call answered 403 FORBIDDEN (FR-75, NFR-18: the server decides): the profile read from
// the database said administrator, the access token (roles at sign-in) did not, or the role was
// taken away. "Check again" renews the token, which reads the roles again (services/auth.py
// refresh), then asks once more; a session that ended sends the guard to sign-in.
import { useState } from 'react';

import { useApp } from '../app/context';
import styles from '../discover/discover.module.css';
import { adminEnIN } from '../strings/admin-en-IN';

export function AccessChanged({ onRetry }: { onRetry: () => Promise<unknown> }) {
  const a = adminEnIN.access;
  const { auth } = useApp();
  const [checking, setChecking] = useState(false);
  const retry = async () => {
    if (checking) return;
    setChecking(true);
    try {
      await auth.refresh();
      await onRetry();
    } finally {
      setChecking(false);
    }
  };
  return (
    <div className={styles.empty} role="alert">
      <p className={styles.emptyHead}>{a.title}</p>
      <p className={styles.hint}>{a.body}</p>
      <button type="button" className={styles.btnQuiet} onClick={() => void retry()} aria-disabled={checking}>
        {checking ? a.checking : a.retry}
      </button>
    </div>
  );
}
