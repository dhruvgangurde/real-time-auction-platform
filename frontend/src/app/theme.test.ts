// The colour theme the viewer chose (System, Light or Dark): kept in localStorage, read safely
// (a blocked or broken storage means System), applied as data-theme on <html>, where the tokens
// (styles/tokens.css) pick it up. public/theme.js applies the same choice before first paint.
import { expect, test } from 'vitest';

import { applyTheme, nextTheme, readTheme, THEME_KEY, writeTheme, type Theme } from './theme';

function memory(initial: Record<string, string> = {}) {
  const values = new Map(Object.entries(initial));
  return {
    getItem: (k: string) => values.get(k) ?? null,
    setItem: (k: string, v: string) => void values.set(k, v),
    removeItem: (k: string) => void values.delete(k),
    values,
  };
}

const broken = {
  getItem: () => {
    throw new Error('blocked');
  },
  setItem: () => {
    throw new Error('blocked');
  },
  removeItem: () => {
    throw new Error('blocked');
  },
};

test('a stored choice is read back; nothing, junk or a broken storage means System', () => {
  expect(readTheme(memory({ [THEME_KEY]: 'dark' }))).toBe('dark');
  expect(readTheme(memory({ [THEME_KEY]: 'light' }))).toBe('light');
  expect(readTheme(memory())).toBe('system');
  expect(readTheme(memory({ [THEME_KEY]: 'sepia' }))).toBe('system');
  expect(readTheme(broken)).toBe('system');
  expect(readTheme(null)).toBe('system');
});

test('a choice is stored; System removes the key; a broken storage is ignored', () => {
  const store = memory();
  writeTheme(store, 'dark');
  expect(store.values.get(THEME_KEY)).toBe('dark');
  writeTheme(store, 'system');
  expect(store.values.has(THEME_KEY)).toBe(false);
  expect(() => writeTheme(broken, 'light')).not.toThrow();
});

test('light and dark set data-theme on the root; System removes it', () => {
  const dataset: Record<string, string | undefined> = {};
  const root = { dataset, removeAttribute: (name: string) => name === 'data-theme' && delete dataset.theme };
  applyTheme(root, 'dark');
  expect(dataset.theme).toBe('dark');
  applyTheme(root, 'light');
  expect(dataset.theme).toBe('light');
  applyTheme(root, 'system');
  expect('theme' in dataset).toBe(false);
});

test('the control steps System, Light, Dark, then System again', () => {
  const order: Theme[] = ['system'];
  for (let i = 0; i < 3; i += 1) order.push(nextTheme(order.at(-1)!));
  expect(order).toEqual(['system', 'light', 'dark', 'system']);
});
