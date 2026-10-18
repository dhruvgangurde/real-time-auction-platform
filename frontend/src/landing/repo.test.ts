// Links to the public repository (brief 8.6, 8.7: from VITE_REPO_URL, hidden while unset).
import { expect, test } from 'vitest';

import { repoLink } from './repo';

test('a file on main in the repository', () => {
  const base = 'https://github.com/dhruvgangurde/real-time-auction-platform';
  expect(repoLink(base, 'loadtest/RESULTS.md')).toBe(`${base}/blob/main/loadtest/RESULTS.md`);
  expect(repoLink(`${base}/`, 'docs/decisions.md')).toBe(`${base}/blob/main/docs/decisions.md`);
  expect(repoLink(base, '')).toBe(base);
});

test('unset, blank or not https: no link at all', () => {
  expect(repoLink(undefined, 'loadtest/RESULTS.md')).toBeNull();
  expect(repoLink('', 'loadtest/RESULTS.md')).toBeNull();
  expect(repoLink('   ', 'loadtest/RESULTS.md')).toBeNull();
  expect(repoLink('http://github.com/x/y', 'a')).toBeNull();
  expect(repoLink('javascript:alert(1)', 'a')).toBeNull();
});
