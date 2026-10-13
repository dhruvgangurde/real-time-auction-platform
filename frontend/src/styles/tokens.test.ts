// tokens.css must carry exactly the brief's section 4.1 table, in both themes, and the
// prefers-color-scheme block must equal the [data-theme="dark"] override.
import { expect, test } from 'vitest';

import brief from '../../../docs/design-brief.md?raw';
import tokens from './tokens.css?raw';

function briefTable(): { light: Record<string, string>; dark: Record<string, string> } {
  const section = brief.slice(brief.indexOf('### 4.1 Tokens'), brief.indexOf('### 4.2 Contrast'));
  const light: Record<string, string> = {};
  const dark: Record<string, string> = {};
  for (const m of section.matchAll(/^\| `(--[a-z0-9-]+)` \| `(#[0-9A-F]{6})` \| `(#[0-9A-F]{6})` \|/gm)) {
    light[m[1]!] = m[2]!;
    dark[m[1]!] = m[3]!;
  }
  return { light, dark };
}

function block(selector: string): Record<string, string> {
  const start = tokens.indexOf(selector);
  expect(start, selector).toBeGreaterThanOrEqual(0);
  const body = tokens.slice(start, tokens.indexOf('}', start));
  const out: Record<string, string> = {};
  for (const m of body.matchAll(/(--[a-z0-9-]+):\s*(#[0-9A-F]{6});/g)) out[m[1]!] = m[2]!;
  return out;
}

const table = briefTable();

test('the brief lists fifteen tokens', () => {
  expect(Object.keys(table.light)).toHaveLength(15);
});

test('the light theme equals the brief', () => {
  expect(block(':root {')).toEqual(table.light);
});

test('the dark override equals the brief', () => {
  expect(block(':root[data-theme="dark"]')).toEqual(table.dark);
});

test('prefers-color-scheme dark equals the dark override and yields to data-theme light', () => {
  expect(block(':root:not([data-theme="light"])')).toEqual(table.dark);
});

test('color-scheme is declared per theme', () => {
  expect(tokens.match(/color-scheme: light;/g)).toHaveLength(1);
  expect(tokens.match(/color-scheme: dark;/g)).toHaveLength(2);
});
