// The category tree for administrators (FR-20, FR-75; schemas/auctions.py CategoryCreate/Update):
// where a category may go (never under itself or its own subcategories: CATEGORY_CYCLE), what the
// API would refuse about a name, a slug or a missing reason, and an edit sending only what changed.
import { expect, test } from 'vitest';

import type { Category } from '../lib/contract';
import { categoryPatch, checkCategory, flatten, parentChoices } from './tree';

const node = (id: number, name: string, parent_id: number | null, children: Category[] = []): Category => ({ id, name, slug: name.toLowerCase(), parent_id, children });
const tree = [node(1, 'Cameras', null, [node(2, 'Film', 1, [node(3, 'Medium', 2)]), node(4, 'Lenses', 1)]), node(5, 'Watches', null)];

test('the tree, flat and in order, each with its depth', () => {
  expect(flatten(tree).map((c) => [c.id, c.depth])).toEqual([[1, 0], [2, 1], [3, 2], [4, 1], [5, 0]]);
});

test('a category may move anywhere but under itself or below itself', () => {
  expect(parentChoices(tree, 2).map((c) => c.id)).toEqual([1, 4, 5]);
  expect(parentChoices(tree, null).map((c) => c.id)).toEqual([1, 2, 3, 4, 5]);
});

test('a name, a URL-safe slug and a reason, as the API requires', () => {
  expect(checkCategory({ name: 'Film', slug: 'film-cameras', reason: 'tidy' })).toEqual({});
  expect(checkCategory({ name: ' ', slug: 'Film Cameras', reason: '' })).toEqual({ name: 'required', slug: 'slug', reason: 'required' });
  expect(checkCategory({ name: 'x'.repeat(101), slug: '-film', reason: 'r' })).toEqual({ name: 'long', slug: 'slug' });
});

test('an edit sends only what changed; moving to the top sends parent null', () => {
  const film = flatten(tree).find((c) => c.id === 2)!;
  expect(categoryPatch(film, { name: 'Film', slug: 'film', parent: '1' })).toEqual({});
  expect(categoryPatch(film, { name: 'Film cameras', slug: 'film', parent: '' })).toEqual({ name: 'Film cameras', parent_id: null });
  expect(categoryPatch(film, { name: 'Film', slug: 'film-cameras', parent: '5' })).toEqual({ slug: 'film-cameras', parent_id: 5 });
});
