// The category tree for administrators (brief 9.9; FR-20, FR-75, UI-8, BR-12): create, rename and
// move with a reason, delete behind a confirmation that names the category and asks a reason. A
// category cannot move under itself (offered nowhere: CATEGORY_CYCLE); SLUG_TAKEN, CATEGORY_IN_USE
// and the rest stay the server's to refuse, said in the code's own words.
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useId, useState, type FormEvent } from 'react';

import { ApiError } from '../api/client';
import { useApp } from '../app/context';
import styles from '../discover/discover.module.css';
import type { Category } from '../lib/contract';
import { ConfirmDialog } from '../room/ConfirmDialog';
import { adminEnIN } from '../strings/admin-en-IN';
import { errorMessage, useStrings } from '../strings/catalogue';
import { AccessChanged } from './AccessChanged';
import own from './admin.module.css';
import { createCategory, deleteCategory, updateCategory } from './api';
import { categoryPatch, checkCategory, parentChoices, type Flat } from './tree';

type Field = 'name' | 'slug' | 'parent' | 'reason';
const codeOf = (e: unknown) => (e instanceof ApiError ? e : new ApiError('SERVICE_UNAVAILABLE', '', 0));

/** New (before null) or an edit of one category; `onDone` with the sentence to say. */
function CategoryForm({ tree, before, onDone, onCancel }: { tree: Category[]; before: Flat | null; onDone: (said: string) => void; onCancel?: () => void }) {
  const s = useStrings();
  const c = adminEnIN.categories;
  const { api } = useApp();
  const id = useId();
  const [problems, setProblems] = useState<Partial<Record<Field, string>>>({});
  const [top, setTop] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending) return;
    const form = event.currentTarget;
    const data = new FormData(form);
    const v = { name: String(data.get('name') ?? ''), slug: String(data.get('slug') ?? ''), parent: String(data.get('parent') ?? ''), reason: String(data.get('reason') ?? '') };
    const found = checkCategory(v);
    const said: Partial<Record<Field, string>> = Object.fromEntries(Object.entries(found).map(([k, p]) => [k, c.problems[p]]));
    const patch = before === null ? null : categoryPatch(before, v);
    setTop(null);
    if (Object.keys(said).length > 0 || (patch !== null && Object.keys(patch).length === 0)) {
      setProblems(said);
      if (patch !== null && Object.keys(patch).length === 0) setTop(c.unchanged);
      setTimeout(() => form.querySelector<HTMLElement>('[aria-invalid="true"]')?.focus(), 0);
      return;
    }
    setPending(true);
    setProblems({});
    try {
      if (before === null) await createCategory(api, { name: v.name.trim(), slug: v.slug, parent_id: v.parent === '' ? null : Number(v.parent), reason: v.reason.trim() });
      else await updateCategory(api, before.id, { ...patch, reason: v.reason.trim() });
      onDone(before === null ? c.created(v.name.trim()) : c.saved(v.name.trim()));
      if (before === null) form.reset();
    } catch (e) {
      const failure = codeOf(e);
      // the refusal next to what it is about: the slug, the parent, or the form as a whole
      const field: Field | null = failure.code === 'SLUG_TAKEN' ? 'slug' : failure.code === 'CATEGORY_CYCLE' || failure.code === 'CATEGORY_NOT_FOUND' ? 'parent' : null;
      if (field === null) setTop(errorMessage(s, failure.code));
      else setProblems({ [field]: errorMessage(s, failure.code) });
    } finally {
      setPending(false);
    }
  }

  const row = (name: Field, label: string, control: (a: { id: string; 'aria-invalid'?: true; 'aria-describedby'?: string }) => React.ReactNode, hint?: string) => {
    const problem = problems[name];
    const described = [hint === undefined ? null : `${id}-${name}-hint`, problem === undefined ? null : `${id}-${name}-error`].filter(Boolean).join(' ');
    return (
      <div className={styles.field}>
        <label className={styles.label} htmlFor={`${id}-${name}`}>
          {label}
        </label>
        {control({ id: `${id}-${name}`, ...(problem === undefined ? {} : { 'aria-invalid': true }), ...(described === '' ? {} : { 'aria-describedby': described }) })}
        {hint === undefined ? null : (
          <p id={`${id}-${name}-hint`} className={styles.hint}>
            {hint}
          </p>
        )}
        {problem === undefined ? null : (
          <p id={`${id}-${name}-error`} className={styles.fieldError}>
            {problem}
          </p>
        )}
      </div>
    );
  };

  return (
    <form className={own.categoryForm} onSubmit={(e) => void submit(e)} noValidate>
      {row('name', c.name, (a) => <input {...a} className={styles.input} name="name" defaultValue={before?.name ?? ''} maxLength={100} autoComplete="off" />)}
      {row('slug', c.slug, (a) => <input {...a} className={styles.input} name="slug" defaultValue={before?.slug ?? ''} maxLength={100} autoComplete="off" spellCheck={false} />, c.slugHint)}
      {row('parent', c.parent, (a) => (
        <select {...a} className={styles.input} name="parent" defaultValue={before?.parent_id === null || before === null ? '' : String(before.parent_id)}>
          <option value="">{c.top}</option>
          {parentChoices(tree, before?.id ?? null).map((p) => (
            <option key={p.id} value={String(p.id)}>
              {' '.repeat(p.depth) + p.name}
            </option>
          ))}
        </select>
      ))}
      {row('reason', adminEnIN.room.reasonLabel, (a) => <textarea {...a} className={styles.input} name="reason" maxLength={500} />, adminEnIN.room.reasonNote)}
      {top === null ? null : (
        <p className={styles.fieldError} role="alert">
          {top}
        </p>
      )}
      <p className={own.pager}>
        <button type="submit" className={styles.btnPrimary} aria-disabled={pending}>
          {before === null ? c.create : c.save}
        </button>
        {onCancel === undefined ? null : (
          <button type="button" className={styles.btnQuiet} onClick={onCancel}>
            {c.cancel}
          </button>
        )}
      </p>
    </form>
  );
}

export function Categories() {
  const s = useStrings();
  const c = adminEnIN.categories;
  const { api } = useApp();
  const client = useQueryClient();
  const tree = useQuery({ queryKey: ['categories'], queryFn: () => api.categories(), staleTime: 0 });
  const [editing, setEditing] = useState<number | null>(null);
  const [removing, setRemoving] = useState<Flat | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  const changed = async (said: string) => {
    await client.invalidateQueries({ queryKey: ['categories'] });
    setEditing(null);
    setDone(said);
  };
  async function remove(node: Flat, reason: string) {
    setError(null);
    try {
      await deleteCategory(api, node.id, reason);
      setRemoving(null);
      await changed(c.removed(node.name));
    } catch (e) {
      setError(errorMessage(s, codeOf(e).code)); // CATEGORY_IN_USE: subcategories or auctions in it
    }
  }

  const branch = (nodes: Category[], depth: number) => (
    <ul className={own.tree}>
      {nodes.map((node) => {
        const flat: Flat = { id: node.id, name: node.name, slug: node.slug, parent_id: node.parent_id, depth };
        return (
          <li key={node.id}>
            <div className={own.node}>
              <p>
                <span className={own.name}>{node.name}</span> <span className={styles.hint}>{node.slug}</span>
              </p>
              <button type="button" className={styles.btnQuiet} aria-label={c.editLabel(node.name)} aria-expanded={editing === node.id} onClick={() => setEditing(editing === node.id ? null : node.id)}>
                {c.edit}
              </button>
              <button
                type="button"
                className={styles.btnQuiet}
                aria-label={c.removeLabel(node.name)}
                onClick={() => {
                  setError(null);
                  setRemoving(flat);
                }}
              >
                {c.remove}
              </button>
            </div>
            {editing === node.id && tree.data !== undefined ? (
              <section aria-label={c.editTitle(node.name)}>
                <CategoryForm tree={tree.data} before={flat} onDone={(said) => void changed(said)} onCancel={() => setEditing(null)} />
              </section>
            ) : null}
            {node.children.length === 0 ? null : branch(node.children, depth + 1)}
          </li>
        );
      })}
    </ul>
  );

  let body;
  if (tree.isError && tree.error instanceof ApiError && tree.error.code === 'FORBIDDEN') {
    body = <AccessChanged onRetry={() => tree.refetch()} />;
  } else if (tree.isError) {
    body = (
      <div className={styles.empty} role="alert">
        <p className={styles.emptyHead}>{errorMessage(s, codeOf(tree.error).code)}</p>
        <button type="button" className={styles.btnQuiet} onClick={() => void tree.refetch()}>
          {s.common.tryAgain}
        </button>
      </div>
    );
  } else if (tree.data === undefined) {
    body = (
      <p className={styles.hint} role="status">
        {s.common.loading}
      </p>
    );
  } else {
    body = (
      <>
        {tree.data.length === 0 ? <p className={styles.emptyHead}>{c.empty}</p> : branch(tree.data, 0)}
        <section className={own.addCategory} aria-labelledby="category-new">
          <h3 id="category-new" className={own.sectionTitle}>
            {c.newTitle}
          </h3>
          <CategoryForm tree={tree.data} before={null} onDone={(said) => void changed(said)} />
        </section>
      </>
    );
  }

  return (
    <section aria-labelledby="categories-title">
      <h2 id="categories-title" className={own.sectionTitle}>
        {c.title}
      </h2>
      {done === null ? null : (
        <p className={styles.hint} role="status">
          {done}
        </p>
      )}
      {body}
      {removing === null ? null : (
        <ConfirmDialog
          title={c.removeTitle(removing.name)}
          body={<p>{c.removeBody}</p>}
          confirmLabel={c.remove}
          dismissLabel={c.keep}
          reason={{ label: adminEnIN.room.reasonLabel, note: adminEnIN.room.reasonNote }}
          error={error}
          onConfirm={(why) => void remove(removing, why)}
          onClose={() => setRemoving(null)}
        />
      )}
    </section>
  );
}
