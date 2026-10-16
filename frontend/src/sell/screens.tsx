// The seller's screens (brief 9.8; FR-11 to FR-19, BR-1, BR-6), behind RequireRole seller. The
// reserve amount appears only here and in the room's seller panel, both the seller's own.
import { useInfiniteQuery, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router';

import { ApiError } from '../api/client';
import { useApp, useMe, useViewerId } from '../app/context';
import { Card } from '../discover/Card';
import styles from '../discover/discover.module.css';
import { useNow } from '../discover/useNow';
import type { AuctionStatus } from '../lib/contract';
import { errorMessage, useStrings } from '../strings/catalogue';
import { sellerEnIN as t } from '../strings/seller-en-IN';
import { createListing, sellerAuctions, updateListing } from './api';
import { sections } from './dashboard';
import { EMPTY, editable, fromDetail, toCreate, toPatch } from './listing';
import { ListingForm } from './ListingForm';
import own from './sell.module.css';

const SKELETONS = [0, 1, 2];
const KIND = {
  draft: 'draft',
  scheduled: 'scheduled',
  live: 'live',
  closing: 'closing',
  closed_sold: 'sold',
  closed_no_sale: 'noSale',
  cancelled: 'cancelled',
} as const satisfies Record<AuctionStatus, string>;
const ENDED = new Set<AuctionStatus>(['closed_sold', 'closed_no_sale', 'cancelled']);

/** /sell/:id: one listing of the seller's own, editable as far as its status allows (FR-17). */
export function Listing() {
  const s = useStrings();
  const p = t.page;
  const { id = '' } = useParams();
  const { api } = useApp();
  const viewer = useViewerId();
  const me = useMe();
  const client = useQueryClient();
  // read again only after this page's own save: a background refetch must not reset the inputs
  const listing = useQuery({ queryKey: ['listing', id, viewer], queryFn: () => api.detail(id), retry: false, staleTime: Infinity, refetchOnWindowFocus: false });
  const categories = useQuery({ queryKey: ['categories'], queryFn: () => api.categories(), staleTime: 300_000 });
  const [version, setVersion] = useState(0);
  const [note, setNote] = useState<string | undefined>(undefined);

  if (listing.isError) {
    const code = listing.error instanceof ApiError ? listing.error.code : 'SERVICE_UNAVAILABLE';
    return (
      <section className={styles.page}>
        <p className={styles.emptyHead} role="alert">
          {errorMessage(s, code)}
        </p>
        <p className={own.actions}>
          {code === 'NOT_FOUND' ? null : (
            <button type="button" className={styles.btnQuiet} onClick={() => void listing.refetch()}>
              {s.common.tryAgain}
            </button>
          )}
          <Link className={styles.link} to="/sell">
            {p.back}
          </Link>
        </p>
      </section>
    );
  }
  if (listing.data === undefined || me.data === undefined) {
    return (
      <p className="sr-only" role="status">
        {s.common.loading}
      </p>
    );
  }
  const d = listing.data;
  const mine = d.seller.id === me.data.id;
  const can = mine ? editable(d.status, d.sequence_no) : 'none';
  const initial = fromDetail(d);
  const where =
    d.status === 'draft' ? p.draftNote : can === 'descriptive' ? p.descriptive : can === 'all' ? null : ENDED.has(d.status) ? p.ended : p.locked;

  return (
    <section className={styles.page}>
      <p>
        <Link className={styles.link} to="/sell">
          {p.back}
        </Link>
      </p>
      <p className={own.status}>{s.cards.status[KIND[d.status]]}</p>
      <h1 className={styles.title}>{d.title}</h1>
      {mine ? null : <p className={styles.lede}>{p.notYours}</p>}
      {mine && where !== null ? <p className={styles.lede}>{where}</p> : null}
      <p>
        <Link className={styles.link} to={`/auctions/${id}`}>
          {p.room}
        </Link>
      </p>
      {mine ? (
        <ListingForm
          key={version}
          initial={initial}
          can={can}
          mode="edit"
          categories={categories.data ?? []}
          submitLabel={p.save}
          {...(note === undefined ? {} : { note })}
          onSave={async (values) => {
            const patch = toPatch(values, initial, can);
            if (Object.keys(patch).length === 0) {
              setNote(p.unchanged);
              return;
            }
            await updateListing(api, id, patch);
            await client.invalidateQueries({ queryKey: ['seller-auctions'] });
            await listing.refetch();
            setVersion((v) => v + 1);
            setNote(p.saved);
          }}
        />
      ) : null}
    </section>
  );
}

/** /sell/new: a new draft (FR-11); then its own page, where photos are added and it is published. */
export function NewListing() {
  const s = useStrings();
  const f = t.form;
  const { api } = useApp();
  const client = useQueryClient();
  const navigate = useNavigate();
  const categories = useQuery({ queryKey: ['categories'], queryFn: () => api.categories(), staleTime: 300_000 });
  return (
    <section className={styles.page}>
      <h1 className={styles.title}>{f.newTitle}</h1>
      <p className={styles.lede}>{f.newLede}</p>
      {categories.isError ? (
        <div className={styles.empty} role="alert">
          <p className={styles.emptyHead}>{errorMessage(s, categories.error instanceof ApiError ? categories.error.code : 'SERVICE_UNAVAILABLE')}</p>
          <button type="button" className={styles.btnQuiet} onClick={() => void categories.refetch()}>
            {s.common.tryAgain}
          </button>
        </div>
      ) : (
        <ListingForm
          initial={EMPTY}
          can="all"
          mode="create"
          categories={categories.data ?? []}
          submitLabel={f.create}
          onSave={async (values) => {
            const id = await createListing(api, toCreate(values));
            await client.invalidateQueries({ queryKey: ['seller-auctions'] });
            await navigate(`/sell/${id}`);
          }}
        />
      )}
    </section>
  );
}

/** /sell: every listing of the seller's own, drafts and cancelled included (FR-19). */
export function Dashboard() {
  const s = useStrings();
  const d = t.dashboard;
  const { api, clock } = useApp();
  const viewer = useViewerId();
  const now = useNow(clock);
  const list = useInfiniteQuery({
    queryKey: ['seller-auctions', viewer],
    queryFn: ({ pageParam }) => sellerAuctions(api, pageParam),
    initialPageParam: 1,
    getNextPageParam: (last) => (last.has_more ? last.page + 1 : undefined),
  });
  const items = list.data?.pages.flatMap((p) => p.items) ?? [];

  let body;
  if (list.isError && list.data === undefined) {
    const code = list.error instanceof ApiError ? list.error.code : 'SERVICE_UNAVAILABLE';
    body = (
      <div className={styles.empty} role="alert">
        <p className={styles.emptyHead}>{errorMessage(s, code)}</p>
        <button type="button" className={styles.btnQuiet} onClick={() => void list.refetch()}>
          {s.common.tryAgain}
        </button>
      </div>
    );
  } else if (list.data === undefined) {
    body = (
      <>
        <p className="sr-only" role="status">
          {s.common.loading}
        </p>
        <ul className={styles.grid} aria-hidden="true">
          {SKELETONS.map((i) => (
            <li key={i} className={styles.skeleton}>
              <div className={styles.media} />
              <div className={styles.bar} />
              <div className={styles.barShort} />
            </li>
          ))}
        </ul>
      </>
    );
  } else if (items.length === 0) {
    body = (
      <div className={styles.empty}>
        <p className={styles.emptyHead}>{d.empty}</p>
        <Link className={styles.btnPrimary} to="/sell/new">
          {d.list}
        </Link>
      </div>
    );
  } else {
    body = (
      <>
        {sections(items).map((section) => (
          <section key={section.key} className={own.section} aria-labelledby={`section-${section.key}`}>
            <h2 id={`section-${section.key}`} className={own.sectionTitle}>
              {d.sections[section.key]}
            </h2>
            <ul className={styles.grid}>
              {section.items.map((c) => (
                <li key={c.id}>
                  <Card card={c} nowMs={now} to={`/sell/${c.id}`} level="h3" />
                </li>
              ))}
            </ul>
          </section>
        ))}
        {list.hasNextPage ? (
          <p className={styles.more}>
            <button type="button" className={styles.btnQuiet} onClick={() => void list.fetchNextPage()} aria-disabled={list.isFetchingNextPage}>
              {list.isFetchingNextPage ? s.common.loading : d.more}
            </button>
          </p>
        ) : null}
      </>
    );
  }

  return (
    <section className={styles.page}>
      <div className={own.head}>
        <div>
          <h1 className={styles.title}>{d.title}</h1>
          <p className={styles.lede}>{d.lede}</p>
        </div>
        {items.length === 0 ? null : (
          <Link className={styles.btnPrimary} to="/sell/new">
            {d.list}
          </Link>
        )}
      </div>
      <div className={styles.listTop}>{body}</div>
    </section>
  );
}
