// The account (brief 9.1, 9.10; FR-9, FR-8, FR-5, UI-1): the profile (the name is the user's to
// change; email, status and roles are not, FR-9; no avatar, gap C1), links to the user's own pages
// by role (at 390 the header has room for icons only), a new password through the reset email
// (FR-8: it signs out every session, this one included, which the page says before sending), and
// sign out for this browser at every width. Email choices stay on the notifications page.
import { useQueryClient } from '@tanstack/react-query';
import { useState, type FormEvent } from 'react';
import { Link } from 'react-router';

import { ApiError } from '../api/client';
import { useApp, useMe } from '../app/context';
import { FormError } from '../auth/Form';
import { SignOutButton } from '../auth/screens';
import styles from '../discover/discover.module.css';
import { accountEnIN as t } from '../strings/account-en-IN';
import { useStrings } from '../strings/catalogue';
import own from './account.module.css';
import { updateName } from './api';
import { accountLinks, nameProblem, type AccountLink } from './profile';

const HREF: Record<AccountLink, string> = { bids: '/account/bids', listings: '/sell', admin: '/admin', email: '/notifications#email-title' };
const failure = (e: unknown) => (e instanceof ApiError ? e : new ApiError('SERVICE_UNAVAILABLE', '', 0));

function NameForm({ name }: { name: string }) {
  const p = t.profile;
  const { api } = useApp();
  const client = useQueryClient();
  const [problem, setProblem] = useState<string | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [pending, setPending] = useState(false);
  const [saved, setSaved] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending) return;
    const input = event.currentTarget.elements.namedItem('name') as HTMLInputElement;
    const found = nameProblem(input.value);
    setSaved(false);
    setError(null);
    if (found !== null) {
      setProblem(p.problems[found]);
      input.focus();
      return;
    }
    setProblem(null);
    setPending(true);
    try {
      await updateName(api, input.value.trim());
      await client.invalidateQueries({ queryKey: ['me'] }); // the header shows the new name
      setSaved(true);
    } catch (e) {
      setError(failure(e));
    } finally {
      setPending(false);
    }
  }

  return (
    <form onSubmit={(e) => void submit(e)} noValidate>
      <div className={styles.field}>
        <label className={styles.label} htmlFor="account-name">
          {p.name}
        </label>
        <input
          id="account-name"
          className={styles.input}
          name="name"
          defaultValue={name}
          maxLength={200}
          autoComplete="name"
          aria-invalid={problem === null ? undefined : true}
          aria-describedby={problem === null ? 'account-name-hint' : 'account-name-hint account-name-error'}
        />
        <p id="account-name-hint" className={styles.hint}>
          {p.nameHint}
        </p>
        {problem === null ? null : (
          <p id="account-name-error" className={styles.fieldError}>
            {problem}
          </p>
        )}
      </div>
      <FormError error={error} />
      <p className={own.actions}>
        <button type="submit" className={styles.btnPrimary} aria-disabled={pending}>
          {pending ? p.saving : p.save}
        </button>
        {saved ? (
          <span className={styles.hint} role="status">
            {p.saved}
          </span>
        ) : null}
      </p>
    </form>
  );
}

function NewPassword({ email }: { email: string }) {
  const w = t.password;
  const { api } = useApp();
  const [state, setState] = useState<'idle' | 'sending' | 'sent'>('idle');
  const [error, setError] = useState<ApiError | null>(null);
  const send = async () => {
    if (state === 'sending') return;
    setState('sending');
    setError(null);
    try {
      await api.forgotPassword(email); // FR-8: always the same answer; the link comes by email
      setState('sent');
    } catch (e) {
      setError(failure(e));
      setState('idle');
    }
  };
  return (
    <>
      <p className={styles.lede}>{w.note}</p>
      <FormError error={error} />
      <p className={own.actions}>
        <button type="button" className={styles.btnQuiet} onClick={() => void send()} aria-disabled={state === 'sending'}>
          {state === 'sending' ? w.sending : w.send}
        </button>
        {state === 'sent' ? (
          <span className={styles.hint} role="status">
            {w.sent(email)}
          </span>
        ) : null}
      </p>
    </>
  );
}

/** /account */
export function Profile() {
  const s = useStrings();
  const p = t.profile;
  const me = useMe();
  if (me.data === undefined) {
    return (
      <p className={styles.hint} role="status">
        {s.common.loading}
      </p>
    );
  }
  const who = me.data;
  const status = (p.statuses as Record<string, string | undefined>)[who.status] ?? who.status;
  const roles = who.roles.map((r) => (p.roleNames as Record<string, string | undefined>)[r] ?? r).join(', ');
  return (
    <section className={styles.page}>
      <h1 className={styles.title}>{t.title}</h1>
      <section className={own.block} aria-labelledby="account-profile">
        <h2 id="account-profile" className={own.blockTitle}>
          {p.title}
        </h2>
        <NameForm name={who.full_name} />
        <dl className={own.facts}>
          <div>
            <dt>{p.email}</dt>
            <dd>
              {who.email}
              <span className={styles.hint}>{p.emailNote}</span>
            </dd>
          </div>
          <div>
            <dt>{p.status}</dt>
            <dd>{status}</dd>
          </div>
          <div>
            <dt>{p.roles}</dt>
            <dd>{roles}</dd>
          </div>
        </dl>
      </section>
      <section className={own.block} aria-labelledby="account-links">
        <h2 id="account-links" className={own.blockTitle}>
          {t.links.title}
        </h2>
        <ul className={own.links}>
          {accountLinks(who.roles).map((link) => (
            <li key={link}>
              <Link className={styles.link} to={HREF[link]}>
                {t.links[link]}
              </Link>
            </li>
          ))}
        </ul>
      </section>
      <section className={own.block} aria-labelledby="account-password">
        <h2 id="account-password" className={own.blockTitle}>
          {t.password.title}
        </h2>
        <NewPassword email={who.email} />
      </section>
      <section className={own.block} aria-labelledby="account-signout">
        <h2 id="account-signout" className={own.blockTitle}>
          {t.signOut.title}
        </h2>
        <p className={styles.lede}>{t.signOut.note}</p>
        <p className={own.actions}>
          <SignOutButton className={styles.btnQuiet} />
        </p>
      </section>
    </section>
  );
}
