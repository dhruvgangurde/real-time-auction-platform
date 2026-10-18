// The administrator's overview (FR-81, gap A1; NFR-8): find a user, the health of Postgres, Redis
// and the task broker, the counters (bids in the last minute, rejections by reason, connections
// per server, background queues, reconciliation gaps, server errors) and the newest audit
// entries. Read every 5 seconds while the page is open; react-query stops while the tab is hidden.
import { useQuery } from '@tanstack/react-query';
import type { FormEvent } from 'react';
import { Link, useNavigate } from 'react-router';

import { ApiError } from '../api/client';
import { useApp, useViewerId } from '../app/context';
import styles from '../discover/discover.module.css';
import { formatDay, formatLocal } from '../lib/clock';
import { adminEnIN } from '../strings/admin-en-IN';
import { errorMessage, useStrings } from '../strings/catalogue';
import { AccessChanged } from './AccessChanged';
import own from './admin.module.css';
import { Entry } from './Audit';
import { overview, overviewLines } from './overview';

const POLL_MS = 5_000;
const at = (ms: number) => `${formatDay(ms)}, ${formatLocal(ms, { seconds: true })}`;

function Figures({ title, lines }: { title: string; lines: string[] }) {
  return (
    <div className={own.figure}>
      <h3 className={own.figureTitle}>{title}</h3>
      {lines
        .filter((line) => line !== '')
        .map((line) => (
          <p key={line} className="tabular">
            {line}
          </p>
        ))}
    </div>
  );
}

export function OverviewScreen() {
  const s = useStrings();
  const t = adminEnIN.overview;
  const { api } = useApp();
  const viewer = useViewerId();
  const navigate = useNavigate();
  const read = useQuery({ queryKey: ['admin-overview', viewer], queryFn: () => overview(api), refetchInterval: POLL_MS });

  const search = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const q = String(new FormData(event.currentTarget).get('q') ?? '').trim();
    void navigate(q === '' ? '/admin/users' : `/admin/users?q=${encodeURIComponent(q)}`);
  };

  let body;
  if (read.isError && read.error instanceof ApiError && read.error.code === 'FORBIDDEN') {
    body = <AccessChanged onRetry={() => read.refetch()} />;
  } else if (read.isError && read.data === undefined) {
    body = (
      <div className={styles.empty} role="alert">
        <p className={styles.emptyHead}>{t.failed}</p>
        <p className={styles.hint}>{errorMessage(s, read.error instanceof ApiError ? read.error.code : 'SERVICE_UNAVAILABLE')}</p>
        <button type="button" className={styles.btnQuiet} onClick={() => void read.refetch()}>
          {s.common.tryAgain}
        </button>
      </div>
    );
  } else if (read.data === undefined) {
    body = (
      <p className={styles.hint} role="status">
        {s.common.loading}
      </p>
    );
  } else {
    const o = read.data;
    const lines = overviewLines(o, at);
    body = (
      <>
        <p className={`${styles.hint} tabular`}>{t.readAt(formatLocal(o.server_time_ms, { seconds: true }))}</p>
        <h3 className={own.figureTitle}>{t.health}</h3>
        <ul className={own.health}>
          {lines.health.map((h) => (
            <li key={h.name} className={h.ok ? undefined : own.unhealthy}>
              <span>{h.name}</span> <strong>{h.state}</strong>
            </li>
          ))}
        </ul>
        <div className={own.figures}>
          <Figures title={t.bids} lines={[lines.accepted, lines.rejected, lines.reasons]} />
          <Figures title={t.sockets} lines={lines.sockets} />
          <Figures title={t.queues} lines={lines.queues} />
          <Figures title={t.reconciliation} lines={lines.reconciliation} />
          <Figures title={t.errors} lines={[lines.errors]} />
        </div>
        <h3 className={own.figureTitle}>{t.recent}</h3>
        <ol className={own.rows}>
          {o.recent_audit.map((entry) => (
            <Entry key={entry.id} entry={entry} />
          ))}
        </ol>
        <p>
          <Link className={styles.link} to="/admin/audit">
            {t.allAudit}
          </Link>
        </p>
      </>
    );
  }

  return (
    <section aria-labelledby="overview-title">
      <h2 id="overview-title" className={own.sectionTitle}>
        {t.title}
      </h2>
      <form className={own.filters} onSubmit={search} role="search" aria-label={t.search}>
        <div className={styles.field}>
          <label className={styles.label} htmlFor="overview-q">
            {t.searchLabel}
          </label>
          <input id="overview-q" className={styles.input} type="search" name="q" maxLength={200} autoComplete="off" spellCheck={false} />
        </div>
        <p className={own.filterSubmit}>
          <button type="submit" className={styles.btnQuiet}>
            {t.searchButton}
          </button>
        </p>
      </form>
      {body}
    </section>
  );
}
