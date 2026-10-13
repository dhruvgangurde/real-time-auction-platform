// brief 13 item 23: the token is read from the fragment and removed before any render.
import { expect, test } from 'vitest';

import { takeTokenFromHash } from './token';

test('reads the token from the fragment and strips the fragment', () => {
  const replaced: string[] = [];
  const out = takeTokenFromHash({ hash: '#token=abc-DEF_123', pathname: '/verify-email', search: '' }, (u) => replaced.push(u));
  expect(out).toEqual({ token: 'abc-DEF_123' });
  expect(replaced).toEqual(['/verify-email']);
});

test('keeps the query, strips any other fragment, and reports a missing token', () => {
  const replaced: string[] = [];
  expect(takeTokenFromHash({ hash: '#other=1', pathname: '/reset-password', search: '?x=1' }, (u) => replaced.push(u))).toEqual({ token: null });
  expect(replaced).toEqual(['/reset-password?x=1']);
  expect(takeTokenFromHash({ hash: '', pathname: '/reset-password', search: '' }, (u) => replaced.push(u))).toEqual({ token: null });
  expect(replaced).toHaveLength(1); // nothing to strip, nothing replaced
});

test('a token in the query is never read: only the fragment counts', () => {
  expect(takeTokenFromHash({ hash: '', pathname: '/verify-email', search: '?token=leaked' }, () => undefined)).toEqual({ token: null });
});
