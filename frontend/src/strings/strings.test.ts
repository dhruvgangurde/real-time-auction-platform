// OR-11: the catalogue covers exactly the error codes of architecture.md section 9 (except the
// internal NOT_INITIALISED and the HTTP_<status> pattern) and the close codes of section 10.
import { expect, test } from 'vitest';

import architecture from '../../../docs/architecture.md?raw';
import { errorMessage } from './catalogue';
import { enIN } from './en-IN';

const NOT_CODES = new Set(['HTTP', 'HTTP_', 'NOT_INITIALISED']);

function section(from: string, to: string): string {
  return architecture.slice(architecture.indexOf(from), architecture.indexOf(to));
}

test('every documented error code has a message, and no undocumented one', () => {
  const text = section('## 9. Error codes', '## 10. WebSocket close codes');
  const documented = new Set(
    [...text.matchAll(/\b[A-Z][A-Z_]{3,}\b/g)].map((m) => m[0]).filter((c) => !NOT_CODES.has(c)),
  );
  expect([...documented].sort()).toEqual(Object.keys(enIN.errors).sort());
  expect(documented.size).toBe(45);
});

test('every documented close code has a message, and no undocumented one', () => {
  const text = section('## 10. WebSocket close codes', '## 11. Pseudonyms');
  const documented = [...text.matchAll(/^\| (\d{4}) \|/gm)].map((m) => Number(m[1]));
  expect(documented.sort()).toEqual(Object.keys(enIN.close).map(Number).sort());
  expect(documented).toHaveLength(13);
});

test('no message is empty and none leaks a placeholder', () => {
  const leaves: string[] = [];
  const walk = (value: unknown): void => {
    if (typeof value === 'string') leaves.push(value);
    else if (typeof value === 'function') return;
    else if (value && typeof value === 'object') Object.values(value).forEach(walk);
  };
  walk(enIN);
  expect(leaves.length).toBeGreaterThan(60);
  for (const s of leaves) {
    expect(s.trim()).not.toBe('');
    expect(s).not.toMatch(/\{\w+\}|lorem/i);
  }
});

test('an unknown code gets the plain fallback, never a crash', () => {
  expect(errorMessage(enIN, 'BID_TOO_LOW')).toBe(enIN.errors.BID_TOO_LOW);
  expect(errorMessage(enIN, 'SOMETHING_NEW')).toBe(enIN.unknownError);
});

test('interpolations take formatted amounts and counts', () => {
  expect(enIN.room.minNext('₹12,000')).toContain('₹12,000');
  expect(enIN.room.outbid('₹12,500', '₹13,000')).toMatch(/₹12,500.*₹13,000/);
  expect(enIN.room.participants(1)).toContain('1');
  expect(enIN.nav.unread(2)).toBe('2 unread');
});

test('a catalogue sentence never renders with an amount, time or other value missing', () => {
  const sentences: [string, (...args: unknown[]) => unknown][] = [];
  const walk = (value: unknown, path: string): void => {
    if (typeof value === 'function') sentences.push([path, value as (...args: unknown[]) => unknown]);
    else if (value !== null && typeof value === 'object' && !Array.isArray(value)) {
      for (const [key, inner] of Object.entries(value)) walk(inner, `${path}.${key}`);
    }
  };
  walk(enIN, 'enIN');
  expect(sentences.length).toBeGreaterThan(60);
  for (const [path, sentence] of sentences) {
    for (let i = 0; i < sentence.length; i += 1) {
      const args = Array.from({ length: sentence.length }, (_, j) => (j === i ? '' : 'x'));
      expect(sentence(...args), `${path} with argument ${i} missing`).toBe('');
    }
  }
});

test('the 4004 copy never claims another device (4004 also follows suspension and password reset)', () => {
  const texts: string[] = [];
  const walk = (value: unknown): void => {
    if (typeof value === 'string') texts.push(value);
    else if (typeof value === 'function') texts.push(String((value as (...a: unknown[]) => unknown)(...Array.from({ length: value.length }, () => 'x'))));
    else if (value !== null && typeof value === 'object') Object.values(value).forEach(walk);
  };
  walk(enIN);
  expect(texts.filter((t) => /another device/i.test(t))).toEqual([]);
  expect(`${enIN.roomUi.bid.signedOutHead}. ${enIN.roomUi.bid.signedOutNote}`).toBe('You were signed out. Sign in again to bid.');
});

test('the payment-due line does not repeat its row label ("Payment due" already heads the row)', () => {
  const p = enIN.notificationsPage;
  const line = p.text.paymentDue('₹9,000', 'Zenit E');
  expect(line).toBe('₹9,000 for Zenit E.');
  expect(line.toLowerCase().startsWith(p.labels.payment_due.toLowerCase())).toBe(false);
});
