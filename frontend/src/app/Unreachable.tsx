// The load-time refresh hit a network error or a 5xx (brief 9.1): the session may still be
// alive, so this offers a retry instead of a sign-in. No router: the payment document uses it too.
import { useState } from 'react';

import { useStrings } from '../strings/catalogue';
import { useApp } from './context';
import styles from './Home.module.css';

export function Unreachable() {
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

