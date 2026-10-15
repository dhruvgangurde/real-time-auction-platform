// Sign-in returns to `?next=` only when it is a path on this site (the payment document sends its
// viewers through sign-in this way). An open redirect would hand a phishing page our sign-in.
import { expect, test } from 'vitest';

import { safeNext } from './next';

test('a path starting with a single slash is kept as given', () => {
  for (const path of ['/', '/auctions', '/auctions/00000000-0000-0000-0000-000000000000/pay', '/auctions?q=a%2Fb', '/a/b//c?d=//e']) {
    expect(safeNext(path), path).toBe(path);
  }
});

test('anything else is refused: no next, other hosts, backslashes, schemes, control characters', () => {
  const refused = [
    '', // empty
    'auctions', // relative
    ' /auctions', // leading space
    '//evil.example', // protocol-relative host
    '///evil.example',
    '/\\evil.example', // a backslash is a slash to browsers
    '\\\\evil.example',
    '/\\/evil.example',
    '/x\\y',
    'https://evil.example',
    'http:evil.example',
    'javascript:alert(1)',
    'JavaScript:alert(1)',
    'data:text/html,hi',
    '/\t/evil.example', // browsers strip tabs and newlines: this is //evil.example
    '/\n/evil.example',
  ];
  for (const raw of refused) expect(safeNext(raw), JSON.stringify(raw)).toBeNull();
  expect(safeNext(null)).toBeNull();
});

test('anything that decodes to a refused form is refused, at any depth of encoding', () => {
  const refused = [
    '%2F%2Fevil.example', // //evil.example
    '/%2Fevil.example', // //evil.example
    '/%2fevil.example',
    '/%5Cevil.example', // /\evil.example
    '/%5cevil.example',
    '/x%5Cy',
    '/%252F%252Fevil.example', // twice encoded: ///evil.example
    '/%25252F%25252Fevil.example', // three times
    '/%09/evil.example', // a tab
    '/%0D%0A/evil.example',
    '%6Aavascript:alert(1)', // javascript:
    '/%', // malformed escape
    '/%E0%A4%A',
  ];
  for (const raw of refused) expect(safeNext(raw), raw).toBeNull();
});
