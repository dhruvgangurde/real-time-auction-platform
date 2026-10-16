// The users list (brief 9.9; FR-76): search by name or email, role and status filters and the page
// in the URL, newest first. A row says who, their status and roles, and when they joined.
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import type { FormEvent } from 'react';
import { Link, useSearchParams } from 'react-router';

import { ApiError } from '../api/client';
import { useApp, useMe, useViewerId } from '../app/context';
import styles from '../discover/discover.module.css';
import { formatDay } from '../lib/clock';
import { adminEnIN } from '../strings/admin-en-IN';
import { errorMessage, useStrings } from '../strings/catalogue';
import own from './admin.module.css';
import { listUsers, type AdminUser } from './api';
import { usersQuery } from './filters';

const FILTERS = ['q', 'role', 'status'] as const;

export function Users() {
  const s = useStrings();
  const u = adminEnIN.users;
  const { api } = useApp();
  const viewer = useViewerId();
  const me = useMe();
  const [search, setSearch] = useSearchParams();
  const f = usersQuery(search);
  const list = useQuery({
    queryKey: ['admin-users', viewer, f.api],
    queryFn: () => listUsers(api, new URLSearchParams(f.api)),
    placeholderData: keepPreviousData,
  });

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const next = new URLSearchParams();
    for (const name of FILTERS) {
      const value = String(data.get(name) ?? '').trim();
      if (value !== '') next.set(name, value);
    }
    setSearch(next); // a new search starts at page 1
  };
  const pageHref = (n: number) => {
    const next = new URLSearchParams(search);
    if (n === 1) next.delete('page');
    else next.set('page', String(n));
    return `?${next.toString()}`;
  };

  let body;
  if (list.isError && list.data === undefined) {
    body = (
      <div className={styles.empty} role="alert">
        <p className={styles.emptyHead}>{errorMessage(s, list.error instanceof ApiError ? list.error.code : 'SERVICE_UNAVAILABLE')}</p>
        <button type="button" className={styles.btnQuiet} onClick={() => void list.refetch()}>
          {s.common.tryAgain}
        </button>
      </div>
    );
  } else if (list.data === undefined) {
    body = (
      <p className={styles.hint} role="status">
        {s.common.loading}
      </p>
    );
  } else if (list.data.items.length === 0) {
    body = <p className={styles.emptyHead}>{u.empty}</p>;
  } else {
    body = (
      <ul className={own.rows}>
        {list.data.items.map((person: AdminUser) => (
          <li key={person.id} className={own.row}>
            <div className={own.who}>
              <p className={own.name}>
                {person.full_name} {person.id === me.data?.id ? <span className={styles.hint}>{u.you}</span> : null}
              </p>
              <p className={styles.hint}>{person.email}</p>
            </div>
            <p className={own.facts}>
              <span>{u.statuses[person.status]}</span>
              <span className={styles.hint}>{person.roles.map((r) => u.roles[r]).join(', ')}</span>
              <span className={`${styles.hint} tabular`}>{u.joined(formatDay(person.created_at_ms))}</span>
            </p>
          </li>
        ))}
      </ul>
    );
  }

  return (
    <section aria-labelledby="users-title">
      <h2 id="users-title" className={own.sectionTitle}>
        {u.title}
      </h2>
      <form key={search.toString()} className={own.filters} onSubmit={submit} role="search">
        <div className={styles.field}>
          <label className={styles.label} htmlFor="users-q">
            {u.q}
          </label>
          <input id="users-q" className={styles.input} type="search" name="q" defaultValue={f.q} maxLength={200} autoComplete="off" spellCheck={false} />
        </div>
        <div className={styles.field}>
          <label className={styles.label} htmlFor="users-role">
            {u.role}
          </label>
          <select id="users-role" className={styles.input} name="role" defaultValue={f.role}>
            <option value="">{u.anyRole}</option>
            {(['bidder', 'seller', 'admin'] as const).map((r) => (
              <option key={r} value={r}>
                {u.roles[r]}
              </option>
            ))}
          </select>
        </div>
        <div className={styles.field}>
          <label className={styles.label} htmlFor="users-status">
            {u.status}
          </label>
          <select id="users-status" className={styles.input} name="status" defaultValue={f.status}>
            <option value="">{u.anyStatus}</option>
            {(['pending_verification', 'active', 'suspended'] as const).map((st) => (
              <option key={st} value={st}>
                {u.statuses[st]}
              </option>
            ))}
          </select>
        </div>
        <p className={own.filterSubmit}>
          <button type="submit" className={styles.btnPrimary}>
            {u.search}
          </button>
        </p>
      </form>
      {body}
      {list.data === undefined ? null : (
        <p className={own.pager}>
          {f.page > 1 ? (
            <Link className={styles.link} to={pageHref(f.page - 1)}>
              {u.previous}
            </Link>
          ) : null}
          <span className={`${styles.hint} tabular`}>{u.page(f.page)}</span>
          {list.data.has_more ? (
            <Link className={styles.link} to={pageHref(f.page + 1)}>
              {u.next}
            </Link>
          ) : null}
        </p>
      )}
    </section>
  );
}
