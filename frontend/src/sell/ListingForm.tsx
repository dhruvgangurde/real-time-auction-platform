// The listing form (brief 9.8; FR-11, FR-12, FR-14, FR-17, OR-1), for a new listing and for the
// listing page. Uncontrolled inputs; on submit the text is checked the way the server would
// (listing.ts), each problem shown under its input and the first focused; the server's own `fields`
// land the same way, the code's message at the top. What the status no longer allows is disabled.
// Leaving with unsaved changes asks first (web guidelines: a router guard and beforeunload).
import { useEffect, useId, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { useBlocker } from 'react-router';

import { ApiError } from '../api/client';
import { useApp } from '../app/context';
import { FormError } from '../auth/Form';
import styles from '../discover/discover.module.css';
import { HelpDrawer } from '../help/HelpDrawer';
import type { Category } from '../lib/contract';
import { ConfirmDialog } from '../room/ConfirmDialog';
import { useStrings, type HelpTopic } from '../strings/catalogue';
import { sellerEnIN as t } from '../strings/seller-en-IN';
import { EMPTY, fromServer, validate, type Editable, type ListingValues, type Problems } from './listing';
import own from './sell.module.css';

const KEYS = Object.keys(EMPTY) as (keyof ListingValues)[];
const ZONE = Intl.DateTimeFormat().resolvedOptions().timeZone;

/** The form's text; a disabled input is not in the form data, so it keeps its stored value. */
function read(form: HTMLFormElement, base: ListingValues): ListingValues {
  const data = new FormData(form);
  const values = { ...base };
  for (const key of KEYS) {
    const value = data.get(key);
    if (typeof value === 'string') values[key] = value;
  }
  return values;
}

function options(tree: Category[], depth = 0): { id: number; label: string }[] {
  return tree.flatMap((c) => [{ id: c.id, label: ' '.repeat(depth) + c.name }, ...options(c.children, depth + 1)]);
}

type Control = { id: string; 'aria-invalid'?: true; 'aria-describedby'?: string };

/** The topic's question, opening its help (UD-4; brief 9.1: reachable from the control it explains). */
function HelpLink({ topic, onHelp }: { topic: HelpTopic; onHelp: (t: HelpTopic) => void }) {
  const s = useStrings();
  return (
    <button type="button" className={own.helpLink} onClick={() => onHelp(topic)}>
      {s.help.topics[topic].question}
    </button>
  );
}

function Row({
  label,
  hint,
  problem,
  control,
  help,
}: {
  label: string;
  hint?: string;
  problem: string | undefined;
  control: (a: Control) => ReactNode;
  help?: ReactNode;
}) {
  const id = useId();
  const described = [hint === undefined ? null : `${id}-hint`, problem === undefined ? null : `${id}-error`].filter(Boolean).join(' ');
  return (
    <div className={styles.field}>
      <label className={styles.label} htmlFor={id}>
        {label}
      </label>
      {control({ id, ...(problem === undefined ? {} : { 'aria-invalid': true }), ...(described === '' ? {} : { 'aria-describedby': described }) })}
      {hint === undefined ? null : (
        <p id={`${id}-hint`} className={styles.hint}>
          {hint}
        </p>
      )}
      {help}
      {problem === undefined ? null : (
        <p id={`${id}-error`} className={styles.fieldError}>
          {problem}
        </p>
      )}
    </div>
  );
}

export function ListingForm({
  initial,
  can,
  mode,
  categories,
  submitLabel,
  onSave,
  note,
}: {
  initial: ListingValues;
  can: Editable;
  mode: 'create' | 'edit';
  categories: Category[];
  submitLabel: string;
  onSave: (values: ListingValues) => Promise<void>;
  note?: string;
}) {
  const f = t.form;
  const { clock } = useApp();
  const [problems, setProblems] = useState<Problems>({});
  const [error, setError] = useState<ApiError | null>(null);
  const [pending, setPending] = useState(false);
  const [helpTopic, setHelpTopic] = useState<HelpTopic | null>(null);
  // a ref, read when a navigation starts: a successful save clears it before its own navigation
  const dirty = useRef(false);
  const blocker = useBlocker(({ currentLocation, nextLocation }) => dirty.current && currentLocation.pathname !== nextLocation.pathname);
  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => {
      if (dirty.current) event.preventDefault();
    };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, []);
  const lockTerms = can !== 'all'; // price, schedule, category: draft and scheduled only (FR-17)
  const lockText = can === 'none';
  const said = (key: keyof Problems) => {
    const problem = problems[key];
    return problem === undefined ? undefined : t.problems[problem];
  };

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending) return;
    const form = event.currentTarget;
    const fail = (found: Problems, why: ApiError) => {
      setProblems(found);
      setError(why);
      setTimeout(() => form.querySelector<HTMLElement>('[aria-invalid="true"]')?.focus(), 0); // after the re-render
    };
    const values = read(form, initial);
    // the server checks the start only when it changes (auctions.py update)
    const found = validate(values, clock.now(), { checkStart: mode === 'create' || values.start !== initial.start });
    if (Object.keys(found).length > 0) {
      fail(found, new ApiError('VALIDATION_ERROR', '', 422));
      return;
    }
    setPending(true);
    setProblems({});
    setError(null);
    dirty.current = false;
    try {
      await onSave(values);
    } catch (e) {
      dirty.current = true;
      const failure = e instanceof ApiError ? e : new ApiError('INTERNAL_ERROR', '', 0);
      fail(fromServer(failure.fields ?? {}), failure);
    } finally {
      setPending(false);
    }
  }

  return (
    <form
      className={own.form}
      onSubmit={(e) => void submit(e)}
      onInput={() => {
        dirty.current = true;
      }}
      noValidate
    >
      <Row
        label={f.title}
        problem={said('title')}
        control={(a) => <input {...a} className={styles.input} name="title" defaultValue={initial.title} maxLength={200} autoComplete="off" disabled={lockText} />}
      />
      <Row
        label={f.description}
        hint={f.descriptionHint}
        problem={said('description')}
        control={(a) => <textarea {...a} className={own.textarea} name="description" defaultValue={initial.description} maxLength={10_000} disabled={lockText} />}
      />
      <Row
        label={f.category}
        problem={said('categoryId')}
        control={(a) => (
          <select {...a} className={styles.input} name="categoryId" defaultValue={initial.categoryId} disabled={lockTerms}>
            <option value="">{f.categoryChoose}</option>
            {options(categories).map((o) => (
              <option key={o.id} value={String(o.id)}>
                {o.label}
              </option>
            ))}
          </select>
        )}
      />
      <div className={own.pair}>
        <Row
          label={f.startingPrice}
          hint={f.startingHint}
          problem={said('startingPrice')}
          control={(a) => <input {...a} className={styles.input} name="startingPrice" defaultValue={initial.startingPrice} inputMode="decimal" autoComplete="off" disabled={lockTerms} />}
        />
        <Row
          label={f.minIncrement}
          hint={f.incrementHint}
          help={<HelpLink topic="increment" onHelp={setHelpTopic} />}
          problem={said('minIncrement')}
          control={(a) => <input {...a} className={styles.input} name="minIncrement" defaultValue={initial.minIncrement} inputMode="decimal" autoComplete="off" disabled={lockTerms} />}
        />
      </div>
      <Row
        label={f.reserve}
        hint={f.reserveHint}
        help={<HelpLink topic="reserve" onHelp={setHelpTopic} />}
        problem={said('reserve')}
        control={(a) => <input {...a} className={styles.input} name="reserve" defaultValue={initial.reserve} inputMode="decimal" autoComplete="off" disabled={lockTerms} />}
      />
      <fieldset className={own.fieldset}>
        <legend className={own.legend}>{f.schedule}</legend>
        <div className={own.pair}>
          <Row
            label={f.start}
            problem={said('start')}
            control={(a) => <input {...a} className={styles.input} type="datetime-local" name="start" defaultValue={initial.start} disabled={lockTerms} />}
          />
          <Row
            label={f.end}
            problem={said('end')}
            control={(a) => <input {...a} className={styles.input} type="datetime-local" name="end" defaultValue={initial.end} disabled={lockTerms} />}
          />
        </div>
        <p className={styles.hint}>{f.zone(ZONE)}</p>
      </fieldset>
      <fieldset className={own.fieldset}>
        <legend className={own.legend}>{f.softClose}</legend>
        <div className={own.pair}>
          <Row
            label={f.threshold}
            problem={said('threshold')}
            control={(a) => <input {...a} className={styles.input} name="threshold" defaultValue={initial.threshold} inputMode="numeric" autoComplete="off" disabled={lockTerms} />}
          />
          <Row
            label={f.extension}
            problem={said('extension')}
            control={(a) => <input {...a} className={styles.input} name="extension" defaultValue={initial.extension} inputMode="numeric" autoComplete="off" disabled={lockTerms} />}
          />
        </div>
        <p className={styles.hint}>{f.softCloseHint}</p>
        <HelpLink topic="clock" onHelp={setHelpTopic} />
      </fieldset>
      {lockText ? null : (
        <>
          <FormError error={error} />
          <p className={own.actions}>
            <button type="submit" className={styles.btnPrimary} aria-disabled={pending}>
              {pending ? f.saving : submitLabel}
            </button>
            {note === undefined ? null : (
              <span className={styles.hint} role="status">
                {note}
              </span>
            )}
          </p>
        </>
      )}
      <HelpDrawer topic={helpTopic} onClose={() => setHelpTopic(null)} />
      {blocker.state === 'blocked' ? (
        <ConfirmDialog
          title={f.leaveTitle}
          body={<p>{f.leaveBody}</p>}
          confirmLabel={f.leave}
          dismissLabel={f.stay}
          error={null}
          onConfirm={() => blocker.proceed()}
          onClose={() => blocker.reset()}
        />
      ) : null}
    </form>
  );
}
