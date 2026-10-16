// The users list (brief 9.9; FR-76, FR-75, UI-8, BR-12): search by name or email, role and status
// filters and the page in the URL, newest first. A row says who, their status and roles, and when
// they joined, with what an administrator may do to the account, each behind a confirmation that
// names the person and requires a reason written to the audit log.
import { keepPreviousData, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState, type FormEvent } from 'react';
import { Link, useSearchParams } from 'react-router';

import { ApiError } from '../api/client';
import { useApp, useMe, useViewerId } from '../app/context';
import styles from '../discover/discover.module.css';
import { formatDay } from '../lib/clock';
import { adminEnIN } from '../strings/admin-en-IN';
import { ConfirmDialog } from '../room/ConfirmDialog';
import { errorMessage, useStrings } from '../strings/catalogue';
import { AccessChanged } from './AccessChanged';
import own from './admin.module.css';
import { listUsers, reinstateUser, setRole, suspendUser, type AdminUser } from './api';
import { usersQuery } from './filters';
import { userActions, type PersonAction } from './people';

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
  const client = useQueryClient();
  const p = adminEnIN.people;
  const reason = { label: adminEnIN.room.reasonLabel, note: adminEnIN.room.reasonNote };
  const [asked, setAsked] = useState<{ person: AdminUser; action: PersonAction } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const label = (action: PersonAction) =>
    action.kind === 'suspend' ? p.suspend : action.kind === 'reinstate' ? p.reinstate : (action.kind === 'grant' ? p.grant : p.revoke)(p.roleNoun[action.role]);

  async function run(person: AdminUser, action: PersonAction, why: string) {
    setError(null);
    try {
      if (action.kind === 'suspend') await suspendUser(api, person.id, why);
      else if (action.kind === 'reinstate') await reinstateUser(api, person.id, why);
      else await setRole(api, person.id, action.role, action.kind === 'grant', why);
      await client.invalidateQueries({ queryKey: ['admin-users'] });
      setAsked(null);
      const role = action.kind === 'grant' || action.kind === 'revoke' ? p.roleNoun[action.role] : '';
      setDone(
        action.kind === 'suspend'
          ? p.suspended(person.full_name)
          : action.kind === 'reinstate'
            ? p.reinstated(person.full_name)
            : (action.kind === 'grant' ? p.granted : p.revoked)(role, person.full_name),
      );
    } catch (e) {
      // CANNOT_TARGET_SELF, LAST_ADMIN, NOT_SUSPENDED, FORBIDDEN: the code's own words
      setError(errorMessage(s, e instanceof ApiError ? e.code : 'SERVICE_UNAVAILABLE'));
    }
  }
  const dialog = (person: AdminUser, action: PersonAction) => {
    const role = action.kind === 'grant' || action.kind === 'revoke' ? p.roleNoun[action.role] : '';
    if (action.kind === 'suspend') return { title: p.suspendTitle(person.full_name, person.email), body: p.suspendBody };
    if (action.kind === 'reinstate') return { title: p.reinstateTitle(person.full_name, person.email), body: p.reinstateBody };
    if (action.kind === 'grant') return { title: p.grantTitle(role, person.full_name, person.email), body: p.grantBody };
    return { title: p.revokeTitle(role, person.full_name, person.email), body: p.revokeBody };
  };

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
  if (list.isError && list.error instanceof ApiError && list.error.code === 'FORBIDDEN') {
    body = <AccessChanged onRetry={() => list.refetch()} />;
  } else if (list.isError && list.data === undefined) {
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
            <div className={own.actions}>
              {userActions(person, me.data?.id).map((action) => (
                <button
                  key={action.kind === 'grant' || action.kind === 'revoke' ? `${action.kind}-${action.role}` : action.kind}
                  type="button"
                  className={styles.btnQuiet}
                  aria-label={p.actionFor(label(action), person.full_name)}
                  onClick={() => {
                    setError(null);
                    setDone(null);
                    setAsked({ person, action });
                  }}
                >
                  {label(action)}
                </button>
              ))}
            </div>
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
      {done === null ? null : (
        <p className={styles.hint} role="status">
          {done}
        </p>
      )}
      {body}
      {asked === null ? null : (
        <ConfirmDialog
          title={dialog(asked.person, asked.action).title}
          body={<p>{dialog(asked.person, asked.action).body}</p>}
          confirmLabel={label(asked.action)}
          dismissLabel={p.keep}
          reason={reason}
          error={error}
          onConfirm={(why) => void run(asked.person, asked.action, why)}
          onClose={() => setAsked(null)}
        />
      )}
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
