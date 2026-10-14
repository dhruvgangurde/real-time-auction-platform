// Email choices per notification type (FR-72, FR-74; brief 9.6). In-app notifications always
// arrive (FR-71); email is a switch per type, every type on until changed. Saved as one map.
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState, type FormEvent } from 'react';

import { useApp, useViewerId } from '../app/context';
import type { EmailPreferences as Prefs } from '../lib/contract';
import { useStrings } from '../strings/catalogue';
import styles from './notifications.module.css';
import { fullMap } from './preferences';
import { TYPES } from './rows';

export function EmailPreferences() {
  const s = useStrings();
  const e = s.emailPrefs;
  const { api } = useApp();
  const client = useQueryClient();
  const key = ['email-preferences', useViewerId()];
  const prefs = useQuery({ queryKey: key, queryFn: () => api.emailPreferences() });
  const [saved, setSaved] = useState(false);
  const save = useMutation({
    mutationFn: (map: Prefs) => api.setEmailPreferences(map),
    onSuccess: (map) => {
      client.setQueryData(key, map);
      setSaved(true);
    },
  });

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (save.isPending) return;
    setSaved(false);
    save.mutate(fullMap(new FormData(event.currentTarget).getAll('email').map(String)));
  }

  return (
    <section className={styles.prefs} aria-labelledby="email-title">
      <h2 id="email-title" className={styles.prefsTitle}>
        {e.title}
      </h2>
      <p className={styles.hint}>{e.lede}</p>
      {prefs.isError ? (
        <div className={styles.prefsBody} role="alert">
          <p>{e.error}</p>
          <button type="button" className={styles.btnQuiet} onClick={() => void prefs.refetch()}>
            {s.common.tryAgain}
          </button>
        </div>
      ) : prefs.data === undefined ? (
        <p className={styles.prefsBody}>{s.common.loading}</p>
      ) : (
        <form key={JSON.stringify(prefs.data)} className={styles.prefsBody} onSubmit={submit}>
          <fieldset className={styles.prefsSet}>
            <legend className="sr-only">{e.title}</legend>
            {TYPES.map((t) => (
              <label key={t} className={styles.check}>
                <input type="checkbox" name="email" value={t} defaultChecked={prefs.data[t] ?? true} onChange={() => setSaved(false)} />
                {e.labels[t]}
              </label>
            ))}
          </fieldset>
          <div className={styles.prefsActions}>
            <button type="submit" className={styles.btnPrimary} aria-disabled={save.isPending}>
              {save.isPending ? e.saving : e.save}
            </button>
            <p className={save.isError ? styles.failed : styles.saved} role="status">
              {save.isError ? e.failed : saved ? e.saved : ''}
            </p>
          </div>
        </form>
      )}
    </section>
  );
}
