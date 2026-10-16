// The users list's filters live in the URL (FR-76; web guidelines: URL reflects state): a name or
// email, a role, a status and the page; anything the API would refuse is dropped, never sent.
import { expect, test } from 'vitest';

import { usersQuery } from './filters';

test('the URL becomes the API query: known values kept, defaults left out, the page bounded', () => {
  const q = (s: string) => usersQuery(new URLSearchParams(s));
  expect(q('')).toEqual({ q: '', role: '', status: '', page: 1, api: 'page=1&page_size=20' });
  expect(q('q=+asha+&role=seller&status=suspended&page=3')).toEqual({ q: 'asha', role: 'seller', status: 'suspended', page: 3, api: 'q=asha&role=seller&status=suspended&page=3&page_size=20' });
  expect(q('role=owner&status=gone&page=0').api).toBe('page=1&page_size=20');
  expect(q('page=abc').page).toBe(1);
  expect(q('page=99999').page).toBe(10_000);
  expect(q(`q=${'x'.repeat(250)}`).q).toHaveLength(200);
});
