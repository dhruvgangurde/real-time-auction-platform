// The dependency policy (brief 11.2, slice 1): exact versions only, Node pinned to a range that
// takes patch updates but refuses another major.
import { expect, test } from 'vitest';

import pkg from '../package.json';

const EXACT = /^\d+\.\d+\.\d+$/;

test('every dependency is pinned to an exact version', () => {
  const all = { ...pkg.dependencies, ...pkg.devDependencies };
  for (const [name, version] of Object.entries(all)) {
    expect(version, name).toMatch(EXACT);
  }
});

test('Node is pinned to the 24 line', () => {
  expect(pkg.engines.node).toBe('>=24.19.0 <25');
});
