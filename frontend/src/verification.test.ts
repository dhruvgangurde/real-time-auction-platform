// OR-12: docs/verification.md maps every SRS requirement ID to its verification activities or
// lists it as a gap. This runs the matrix's own check, so npm test fails when a test is added,
// renamed or re-cited without regenerating the page, or when anything cites an ID the SRS lacks.
import { execFileSync } from 'node:child_process';

import { expect, test } from 'vitest';

// @ts-expect-error: a plain .mjs script, no types
import { build } from '../scripts/verification.mjs';

test('the verification matrix is current and cites only SRS IDs', () => {
  const said = execFileSync('node', ['scripts/verification.mjs', '--check'], { encoding: 'utf8' });
  expect(said).toMatch(/^docs\/verification\.md is current: \d+ mapped, \d+ gaps/);
});

test('only test modules count as tests: helpers, fakes and conftest.py cite nothing (audit M12)', () => {
  const page: string = build().page;
  const cited = [...page.matchAll(/backend\/tests\/[\w/]+\.py/g)].map((m) => m[0]);
  expect(cited.length).toBeGreaterThan(0);
  expect(cited.filter((path) => !/\/test_\w+\.py$/.test(path))).toEqual([]);
});
