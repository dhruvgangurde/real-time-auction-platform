// Per-user caches (the watchlist, notifications) are keyed on the user, not the access token: the
// token changes on every refresh, the user does not (core/security.py puts the id in `sub`).
import { expect, test } from 'vitest';

import { meKey, subject } from './context';

const b64url = (s: string) => btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
const jwt = (payload: object) => `${b64url('{"alg":"HS256"}')}.${b64url(JSON.stringify(payload))}.sig`;

test('the user id comes from sub; the same user across two tokens gives the same id', () => {
  const id = '6f1c2a3e-9d1b-4c55-8a77-0e2f3b4c5d6e';
  expect(subject(jwt({ sub: id, sid: 'a', exp: 1 }))).toBe(id);
  expect(subject(jwt({ sub: id, sid: 'b', exp: 2 }))).toBe(id);
});

test('anonymous, malformed or sub-less tokens give null', () => {
  expect(subject(null)).toBeNull();
  expect(subject('not-a-jwt')).toBeNull();
  expect(subject('a.%%%.c')).toBeNull();
  expect(subject(jwt({ sid: 'a' }))).toBeNull();
});

test('the profile query is keyed on the user, so a refresh keeps it and a guarded page stays mounted', () => {
  const id = '6f1c2a3e-9d1b-4c55-8a77-0e2f3b4c5d6e';
  expect(meKey(jwt({ sub: id, sid: 'a', exp: 1 }))).toEqual(meKey(jwt({ sub: id, sid: 'a', exp: 2 })));
  expect(meKey(jwt({ sub: id, sid: 'a', exp: 1 }))).not.toEqual(meKey(jwt({ sub: 'someone-else', sid: 'a', exp: 1 })));
  expect(meKey(null)).toEqual(['me', null]);
});
