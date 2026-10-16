// The category tree for administrators (brief 9.9; FR-20, FR-75): as a flat list with depths for
// the screen, where a category may move (never under itself or its own subcategories, which the
// API refuses as CATEGORY_CYCLE), what it would refuse about a name, slug or reason
// (schemas/auctions.py), and an edit's body with only the changed fields. The server still decides.
import type { Category } from '../lib/contract';

export type Flat = { id: number; name: string; slug: string; parent_id: number | null; depth: number };
export type CategoryProblem = 'required' | 'long' | 'slug';

const SLUG = /^[a-z0-9]+(?:-[a-z0-9]+)*$/; // the API's own pattern, at most 100 characters

export function flatten(tree: Category[], depth = 0): Flat[] {
  return tree.flatMap((c) => [{ id: c.id, name: c.name, slug: c.slug, parent_id: c.parent_id, depth }, ...flatten(c.children, depth + 1)]);
}

function below(tree: Category[], id: number): Set<number> {
  const find = (nodes: Category[]): Category | undefined => nodes.map((c) => (c.id === id ? c : find(c.children))).find((c) => c !== undefined);
  const root = find(tree);
  return new Set(root === undefined ? [] : flatten([root]).map((c) => c.id));
}

/** Where a category may go: anywhere but itself and below itself; any, for a new one. */
export function parentChoices(tree: Category[], id: number | null): Flat[] {
  const out = id === null ? new Set<number>() : below(tree, id);
  return flatten(tree).filter((c) => !out.has(c.id));
}

export function checkCategory(v: { name: string; slug: string; reason: string }): Partial<Record<'name' | 'slug' | 'reason', CategoryProblem>> {
  const p: Partial<Record<'name' | 'slug' | 'reason', CategoryProblem>> = {};
  const name = v.name.trim();
  if (name === '') p.name = 'required';
  else if (name.length > 100) p.name = 'long';
  if (!SLUG.test(v.slug) || v.slug.length > 100) p.slug = 'slug';
  if (v.reason.trim() === '') p.reason = 'required';
  return p;
}

/** Only what changed; a parent of '' moves the category to the top (parent_id null). */
export function categoryPatch(before: Flat, v: { name: string; slug: string; parent: string }): Record<string, unknown> {
  const patch: Record<string, unknown> = {};
  if (v.name.trim() !== before.name) patch.name = v.name.trim();
  if (v.slug !== before.slug) patch.slug = v.slug;
  const parent = v.parent === '' ? null : Number(v.parent);
  if (parent !== before.parent_id) patch.parent_id = parent;
  return patch;
}
