// The audit log (brief 9.9; FR-77, FR-80): filters by actor, what it is about and its id, and a
// date range, in the URL; newest first with "Show more" on the keyset. Each entry says when, what,
// about what (an auction links to its room, which is how a draft is reached: no admin auction list,
// gap A2), by whom and why, with its before and after as a readable diff, a reserve never shown.
import { useInfiniteQuery } from '@tanstack/react-query';
import type { FormEvent } from 'react';
import { Link, useSearchParams } from 'react-router';

import { ApiError } from '../api/client';
import { useApp, useViewerId } from '../app/context';
import styles from '../discover/discover.module.css';
import { formatDay, formatLocal } from '../lib/clock';
import { adminEnIN } from '../strings/admin-en-IN';
import { errorMessage, useStrings } from '../strings/catalogue';
import { AccessChanged } from './AccessChanged';
import own from './admin.module.css';
import { auditLog, type AuditEntry } from './api';
import { TYPES, auditQuery, diffRows } from './auditView';

const at = (ms: number) => `${formatDay(ms)}, ${formatLocal(ms, { seconds: true })}`;
const short = (id: string) => id.slice(0, 8);
const FILTERS = ['actor', 'type', 'id', 'from', 'to'] as const;

export function Entry({ entry }: { entry: AuditEntry }) {
  const a = adminEnIN.audit;
  const known = (TYPES as readonly string[]).includes(entry.entity_type);
  const about = a.entity(known ? a.kinds[entry.entity_type as (typeof TYPES)[number]] : entry.entity_type, short(entry.entity_id));
  const rows = diffRows(entry.before, entry.after, at);
  return (
    <li className={own.entry}>
      <p className={own.entryHead}>
        <span className={`${styles.hint} tabular`}>{at(entry.created_at_ms)}</span>
        <strong>{entry.action.replace(/_/g, ' ')}</strong>
        {entry.entity_type === 'auction' ? (
          <Link className={styles.link} to={`/auctions/${entry.entity_id}`}>
            {about}
          </Link>
        ) : (
          <span>{about}</span>
        )}
      </p>
      <p className={styles.hint}>
        {/* the short id is a link that filters by the whole one */}
        {entry.actor_id === null ? (
          a.system
        ) : (
          <Link className={styles.link} to={`?actor=${entry.actor_id}`} aria-label={a.byActorLabel(entry.actor_id)}>
            {a.by(short(entry.actor_id))}
          </Link>
        )}
        {entry.reason === null ? null : ` ${a.reason(entry.reason)}`}
      </p>
      {rows.length === 0 ? null : (
        <table className={own.diff}>
          <thead>
            <tr>
              <th scope="col">{a.field}</th>
              <th scope="col">{a.before}</th>
              <th scope="col">{a.after}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.key}>
                <th scope="row">{r.key}</th>
                <td>{r.before}</td>
                <td>{r.after}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </li>
  );
}

export function Audit() {
  const s = useStrings();
  const a = adminEnIN.audit;
  const { api } = useApp();
  const viewer = useViewerId();
  const [search, setSearch] = useSearchParams();
  const f = auditQuery(search);
  const log = useInfiniteQuery({
    queryKey: ['admin-audit', viewer, f.api],
    queryFn: ({ pageParam }) => auditLog(api, f.api, pageParam),
    initialPageParam: undefined as number | undefined,
    getNextPageParam: (last) => last.next_before_id ?? undefined,
  });
  const items = log.data?.pages.flatMap((p) => p.items) ?? [];

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const next = new URLSearchParams();
    for (const name of FILTERS) {
      const value = String(data.get(name) ?? '').trim();
      if (value !== '') next.set(name, value);
    }
    setSearch(next);
  };

  let body;
  if (log.isError && log.error instanceof ApiError && log.error.code === 'FORBIDDEN') {
    body = <AccessChanged onRetry={() => log.refetch()} />;
  } else if (log.isError && log.data === undefined) {
    body = (
      <div className={styles.empty} role="alert">
        <p className={styles.emptyHead}>{errorMessage(s, log.error instanceof ApiError ? log.error.code : 'SERVICE_UNAVAILABLE')}</p>
        <button type="button" className={styles.btnQuiet} onClick={() => void log.refetch()}>
          {s.common.tryAgain}
        </button>
      </div>
    );
  } else if (log.data === undefined) {
    body = (
      <p className={styles.hint} role="status">
        {s.common.loading}
      </p>
    );
  } else if (items.length === 0) {
    body = <p className={styles.emptyHead}>{a.empty}</p>;
  } else {
    body = (
      <>
        <ul className={own.rows}>
          {items.map((entry) => (
            <Entry key={entry.id} entry={entry} />
          ))}
        </ul>
        {log.hasNextPage ? (
          <p className={own.pager}>
            <button type="button" className={styles.btnQuiet} onClick={() => void log.fetchNextPage()} aria-disabled={log.isFetchingNextPage}>
              {log.isFetchingNextPage ? s.common.loading : a.more}
            </button>
          </p>
        ) : null}
      </>
    );
  }

  return (
    <section aria-labelledby="audit-title">
      <h2 id="audit-title" className={own.sectionTitle}>
        {a.title}
      </h2>
      <form key={search.toString()} className={own.filters} onSubmit={submit} role="search">
        <div className={styles.field}>
          <label className={styles.label} htmlFor="audit-actor">
            {a.actor}
          </label>
          <input id="audit-actor" className={styles.input} name="actor" defaultValue={f.actor} autoComplete="off" spellCheck={false} />
        </div>
        <div className={styles.field}>
          <label className={styles.label} htmlFor="audit-type">
            {a.type}
          </label>
          <select id="audit-type" className={styles.input} name="type" defaultValue={f.type}>
            <option value="">{a.anyType}</option>
            {TYPES.map((t) => (
              <option key={t} value={t}>
                {a.types[t]}
              </option>
            ))}
          </select>
        </div>
        <div className={styles.field}>
          <label className={styles.label} htmlFor="audit-id">
            {a.id}
          </label>
          <input id="audit-id" className={styles.input} name="id" defaultValue={f.id} maxLength={200} autoComplete="off" spellCheck={false} />
        </div>
        <div className={styles.field}>
          <label className={styles.label} htmlFor="audit-from">
            {a.from}
          </label>
          <input id="audit-from" className={styles.input} type="datetime-local" name="from" defaultValue={f.from} />
        </div>
        <div className={styles.field}>
          <label className={styles.label} htmlFor="audit-to">
            {a.to}
          </label>
          <input id="audit-to" className={styles.input} type="datetime-local" name="to" defaultValue={f.to} />
        </div>
        <p className={own.filterSubmit}>
          <button type="submit" className={styles.btnPrimary}>
            {a.filter}
          </button>
        </p>
      </form>
      {body}
    </section>
  );
}
