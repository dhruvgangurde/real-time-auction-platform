// Discover and search, /auctions (brief 9.2; FR-20 to FR-23, FR-28, FR-30). Everything the viewer
// chose is in the URL (params.ts): a submit pushes one history entry, "Show more" replaces the
// current one, so back, forward and a shared link restore the same list. REST only, no socket
// for anyone (FR-28): prices and end times come from the list, refetched every 15 s while the tab
// is visible (not beyond three pages) and once more when a live card's countdown reaches zero.
import { useInfiniteQuery, useQuery } from '@tanstack/react-query';
import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react';
import { Link, useSearchParams } from 'react-router';

import { ApiError } from '../api/client';
import { useApp } from '../app/context';
import { formatDay, formatLocal } from '../lib/clock';
import type { AuctionCard, Category } from '../lib/contract';
import { formatINR } from '../lib/money';
import { errorMessage, useStrings, type Catalogue } from '../strings/catalogue';
import { WatchButton } from '../watch/WatchButton';
import { Card } from './Card';
import { crossedZero, pollInterval } from './cards';
import styles from './discover.module.css';
import {
  MAX_PAGES,
  MAX_QUERY,
  STATUS_CHOICES,
  apiQuery,
  fromLocalInput,
  parseFilters,
  rangeError,
  toLocalInput,
  toSearch,
  type Filters,
  type ListedStatus,
} from './params';
import { useNow } from './useNow';

type Flat = { id: number; name: string; slug: string; depth: number };
const INDENT = ' '; // an em space per level in the category list
const SKELETONS = [0, 1, 2, 3, 4, 5];
const RUPEES = /^\d{1,14}$/;

function flatten(tree: Category[], depth = 0): Flat[] {
  return tree.flatMap((c) => [{ id: c.id, name: c.name, slug: c.slug, depth }, ...flatten(c.children, depth + 1)]);
}

/** Whole rupees from a form field ("1,500" or "1500"); null when empty, undefined when invalid. */
function wholeRupees(text: string): number | null | undefined {
  const plain = text.trim().replace(/,/g, '');
  if (plain === '') return null;
  return RUPEES.test(plain) && Number.isSafeInteger(Number(plain) * 100) ? Number(plain) : undefined;
}

function summary(f: Filters, category: Flat | null, s: Catalogue): string[] {
  const a = s.discover.active;
  const out: string[] = [];
  if (f.q !== '') out.push(a.search(f.q));
  if (category !== null) out.push(a.category(category.name));
  if (f.status.length > 0) out.push(a.status(f.status.map((x) => s.discover.statusNames[x]).join(', ')));
  const rupees = (n: number) => formatINR(n * 100);
  if (f.min !== null && f.max !== null) out.push(a.price(a.between(rupees(f.min), rupees(f.max))));
  else if (f.min !== null) out.push(a.price(a.from(rupees(f.min))));
  else if (f.max !== null) out.push(a.price(a.upTo(rupees(f.max))));
  if (f.closingBefore !== null) {
    const ms = new Date(f.closingBefore).getTime();
    out.push(a.closing(`${formatDay(ms)}, ${formatLocal(ms)}`));
  }
  return out;
}

export function DiscoverPage() {
  const s = useStrings();
  const d = s.discover;
  const { api, clock } = useApp();
  const [search, setSearch] = useSearchParams();
  const filters = useMemo(() => parseFilters(search), [search]);
  const [open, setOpen] = useState(false); // the filter panel on phones
  const [errors, setErrors] = useState<{ min?: string; max?: string }>({});

  const categories = useQuery({ queryKey: ['categories'], queryFn: () => api.categories(), staleTime: 300_000 });
  const flat = useMemo(() => flatten(categories.data ?? []), [categories.data]);
  const category = filters.category === null ? null : (flat.find((c) => c.slug === filters.category) ?? null);
  const unknownCategory = filters.category !== null && categories.isSuccess && category === null;
  const badRange = rangeError(filters);
  const listKey = toSearch({ ...filters, pages: 1 }).toString();
  const query = useInfiniteQuery({
    queryKey: ['auctions', listKey, category?.id ?? null],
    queryFn: ({ pageParam }) => api.auctions(apiQuery(filters, category?.id ?? null, pageParam)),
    initialPageParam: 1,
    getNextPageParam: (last) => (last.has_more ? last.page + 1 : undefined),
    enabled: !badRange && (filters.category === null || category !== null),
    refetchInterval: (q) => pollInterval(q.state.data?.pages.length ?? 0),
  });

  const loaded = query.data?.pages.length ?? 0;
  const items = useMemo(() => {
    // offset pages over a newest-first list can repeat a card when a listing is published between
    // two pages: keep the first
    const seen = new Set<string>();
    const out: AuctionCard[] = [];
    for (const c of query.data?.pages.flatMap((p) => p.items) ?? []) {
      if (!seen.has(c.id)) {
        seen.add(c.id);
        out.push(c);
      }
    }
    return out;
  }, [query.data]);

  // a link or a reload asking for N pages loads them one after another
  const { hasNextPage, isFetching, fetchNextPage, refetch } = query;
  useEffect(() => {
    if (hasNextPage && !isFetching && loaded < filters.pages) void fetchNextPage();
  }, [hasNextPage, isFetching, loaded, filters.pages, fetchNextPage]);

  // a live card's countdown reached zero: ask again 2 s later, at most once every 5 s
  const now = useNow(clock);
  const prevNow = useRef(now);
  const lastKick = useRef(0);
  const kick = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    const prev = prevNow.current;
    prevNow.current = now;
    if (kick.current !== null || now - lastKick.current < 5_000 || !crossedZero(items, prev, now)) return;
    lastKick.current = now;
    kick.current = setTimeout(() => {
      kick.current = null;
      void refetch();
    }, 2_000);
  }, [now, items, refetch]);
  useEffect(() => () => {
    if (kick.current !== null) clearTimeout(kick.current);
  }, []);

  // "Show more" moves focus to the first new card
  const list = useRef<HTMLUListElement>(null);
  const focusFrom = useRef<number | null>(null);
  useEffect(() => {
    const from = focusFrom.current;
    if (from === null || items.length <= from) return;
    focusFrom.current = null;
    list.current?.querySelectorAll<HTMLAnchorElement>('a[data-card]')[from]?.focus();
  }, [items.length]);

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const text = (name: string) => String(form.get(name) ?? '');
    const min = wholeRupees(text('min'));
    const max = wholeRupees(text('max'));
    const next: Filters = {
      q: text('q').trim().slice(0, MAX_QUERY),
      category: text('category') === '' ? null : text('category'),
      status: STATUS_CHOICES.filter((x) => form.getAll('status').includes(x)),
      min: min ?? null,
      max: max ?? null,
      closingBefore: fromLocalInput(text('closing_before')),
      pages: 1,
    };
    const found: { min?: string; max?: string } = {};
    if (min === undefined) found.min = d.wholeRupees;
    if (max === undefined) found.max = d.wholeRupees;
    else if (rangeError(next)) found.max = d.range;
    setErrors(found);
    if (found.min !== undefined || found.max !== undefined) {
      event.currentTarget.querySelector<HTMLInputElement>(found.min !== undefined ? '#f-min' : '#f-max')?.focus();
      return;
    }
    setSearch(toSearch(next)); // one history entry per search
  }

  function showMore() {
    focusFrom.current = items.length;
    setSearch(toSearch({ ...filters, pages: loaded + 1 }), { replace: true, preventScrollReset: true });
  }

  const active = summary(filters, category, s);
  const count = active.length - (filters.q === '' ? 0 : 1);
  const maxError = errors.max ?? (badRange ? d.range : undefined);
  const listError = query.error instanceof ApiError ? query.error : null;

  let body;
  if (unknownCategory) {
    body = (
      <div className={styles.empty}>
        <p className={styles.emptyHead}>{d.unknownCategory}</p>
        <Link className={styles.btnQuiet} to="/auctions">
          {d.clear}
        </Link>
      </div>
    );
  } else if (badRange) {
    body = null; // the field error says it; nothing is asked
  } else if (categories.isError && filters.category !== null) {
    body = (
      <div className={styles.empty} role="alert">
        <p className={styles.emptyHead}>{d.error}</p>
        <button type="button" className={styles.btnQuiet} onClick={() => void categories.refetch()}>
          {s.common.tryAgain}
        </button>
      </div>
    );
  } else if (query.isError && items.length === 0) {
    body = (
      <div className={styles.empty} role="alert">
        <p className={styles.emptyHead}>
          {listError?.code === 'RATE_LIMITED' && listError.retryAfterMs !== undefined
            ? d.rateLimited(Math.ceil(listError.retryAfterMs / 1000))
            : listError !== null && listError.code !== 'INTERNAL_ERROR'
              ? errorMessage(s, listError.code)
              : d.error}
        </p>
        <button type="button" className={styles.btnQuiet} onClick={() => void refetch()}>
          {s.common.tryAgain}
        </button>
      </div>
    );
  } else if (query.isPending) {
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
        <p className={styles.emptyHead}>{active.length === 0 ? d.empty : d.noMatch}</p>
        {active.length === 0 ? (
          <p className={styles.hint}>{d.emptyNote}</p>
        ) : (
          <Link className={styles.btnQuiet} to="/auctions">
            {d.clear}
          </Link>
        )}
      </div>
    );
  } else {
    body = (
      <>
        <ul className={styles.grid} ref={list}>
          {items.map((c) => (
            <li key={c.id}>
              <Card card={c} nowMs={now} action={<WatchButton card={c} />} />
            </li>
          ))}
        </ul>
        {hasNextPage && loaded >= filters.pages && loaded < MAX_PAGES ? (
          <div className={styles.more}>
            <button type="button" className={styles.btnQuiet} onClick={showMore} aria-disabled={query.isFetchingNextPage}>
              {query.isFetchingNextPage ? d.loadingMore : d.showMore}
            </button>
          </div>
        ) : null}
      </>
    );
  }

  return (
    <section className={styles.page}>
      <h1 className={styles.title}>{d.title}</h1>
      <div className={styles.searchRow} role="search">
        <label className="sr-only" htmlFor="f-q">
          {d.searchLabel}
        </label>
        <input
          key={listKey}
          id="f-q"
          form="discover"
          className={styles.input}
          type="search"
          name="q"
          defaultValue={filters.q}
          placeholder={d.searchPlaceholder}
          maxLength={MAX_QUERY}
          autoComplete="off"
          spellCheck={false}
        />
        <button type="submit" form="discover" className={styles.btnPrimary}>
          {d.search}
        </button>
        <button type="button" className={styles.toggle} aria-expanded={open} aria-controls="discover" onClick={() => setOpen((o) => !o)}>
          {d.filtersToggle(count)}
        </button>
      </div>
      <div className={styles.layout}>
        <form key={listKey} id="discover" className={open ? styles.filtersOpen : styles.filters} onSubmit={submit} noValidate aria-labelledby="f-title">
          <h2 id="f-title" className={styles.filtersTitle}>
            {d.filters}
          </h2>
          <div className={styles.field}>
            <label className={styles.label} htmlFor="f-category">
              {d.category}
            </label>
            {/* remounted when the tree arrives: an uncontrolled default applies only on mount, and before
                the tree there is no option for a linked category to select */}
            <select key={flat.length === 0 ? 'no-tree' : 'tree'} id="f-category" className={styles.input} name="category" defaultValue={category?.slug ?? ''}>
              <option value="">{d.anyCategory}</option>
              {flat.map((c) => (
                <option key={c.id} value={c.slug}>
                  {INDENT.repeat(c.depth) + c.name}
                </option>
              ))}
            </select>
          </div>
          <fieldset className={styles.field}>
            <legend className={styles.label}>{d.status}</legend>
            {STATUS_CHOICES.map((x: ListedStatus) => (
              <label key={x} className={styles.check}>
                <input type="checkbox" name="status" value={x} defaultChecked={filters.status.includes(x)} />
                {d.statusNames[x]}
              </label>
            ))}
            <p className={styles.hint}>{d.statusHint}</p>
          </fieldset>
          <fieldset className={styles.field}>
            <legend className={styles.label}>{d.price}</legend>
            <div className={styles.pair}>
              <div>
                <label className={styles.small} htmlFor="f-min">
                  {d.min}
                </label>
                <input
                  id="f-min"
                  className={styles.input}
                  name="min"
                  inputMode="numeric"
                  autoComplete="off"
                  defaultValue={filters.min ?? ''}
                  aria-invalid={errors.min !== undefined}
                  aria-describedby={errors.min !== undefined ? 'f-min-error' : undefined}
                />
              </div>
              <div>
                <label className={styles.small} htmlFor="f-max">
                  {d.max}
                </label>
                <input
                  id="f-max"
                  className={styles.input}
                  name="max"
                  inputMode="numeric"
                  autoComplete="off"
                  defaultValue={filters.max ?? ''}
                  aria-invalid={maxError !== undefined}
                  aria-describedby={maxError !== undefined ? 'f-max-error' : undefined}
                />
              </div>
            </div>
            {errors.min === undefined ? null : (
              <p id="f-min-error" className={styles.fieldError}>
                {errors.min}
              </p>
            )}
            {maxError === undefined ? null : (
              <p id="f-max-error" className={styles.fieldError}>
                {maxError}
              </p>
            )}
            <p className={styles.hint}>{d.priceHint}</p>
          </fieldset>
          <div className={styles.field}>
            <label className={styles.label} htmlFor="f-closing">
              {d.closingBefore}
            </label>
            <input
              id="f-closing"
              className={styles.input}
              type="datetime-local"
              name="closing_before"
              defaultValue={filters.closingBefore === null ? '' : toLocalInput(filters.closingBefore)}
            />
          </div>
          <div className={styles.filterActions}>
            <button type="submit" className={styles.btnPrimary}>
              {d.apply}
            </button>
            {active.length === 0 ? null : (
              <Link className={styles.link} to="/auctions">
                {d.clear}
              </Link>
            )}
          </div>
        </form>
        <section className={styles.results} aria-label={d.results}>
          {active.length === 0 ? null : (
            <p className={styles.active}>
              <span className={styles.activeLabel}>{d.filteredBy}</span>
              {active.join(', ')}
            </p>
          )}
          {body}
        </section>
      </div>
    </section>
  );
}

export const Component = DiscoverPage;
