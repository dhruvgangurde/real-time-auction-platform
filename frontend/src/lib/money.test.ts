// brief 11.3: its examples are the tests (item 20: integer paise, Indian grouping, the ₹ sign).
import { expect, test } from 'vitest';

import { formatINR, isPaise, parseRupees, rupeesWords } from './money';

test('whole rupees show no decimals', () => {
  expect(formatINR(42500000)).toBe('₹4,25,000');
});

test('paise show only when non-zero', () => {
  expect(formatINR(42500050)).toBe('₹4,25,000.50');
  expect(formatINR(42500005)).toBe('₹4,25,000.05');
});

test('Indian grouping at every scale', () => {
  expect(formatINR(1200000000)).toBe('₹1,20,00,000');
  expect(formatINR(900000)).toBe('₹9,000');
  expect(formatINR(0)).toBe('₹0');
  expect(formatINR(Number.MAX_SAFE_INTEGER)).toBe('₹9,00,71,99,25,47,409.91');
});

test('checkout amounts always show two decimals', () => {
  expect(formatINR(42500000, 'always')).toBe('₹4,25,000.00');
});

test('screen-reader words', () => {
  expect(rupeesWords(42500000)).toBe('4,25,000 rupees');
  expect(rupeesWords(42500050)).toBe('4,25,000 rupees 50 paise');
});

test('formatting refuses anything that is not safe non-negative paise', () => {
  for (const bad of [-1, 1.5, Number.NaN, Number.MAX_SAFE_INTEGER + 1, '100' as unknown as number]) {
    expect(() => formatINR(bad)).toThrow(RangeError);
    expect(isPaise(bad)).toBe(false);
  }
  expect(isPaise(0) && isPaise(Number.MAX_SAFE_INTEGER)).toBe(true);
});

test('parseRupees reads what a user types, as a string', () => {
  expect(parseRupees('4,25,000.5')).toEqual({ ok: true, paise: 42500050 });
  expect(parseRupees('425000')).toEqual({ ok: true, paise: 42500000 });
  expect(parseRupees(' ₹12,000 ')).toEqual({ ok: true, paise: 1200000 });
  expect(parseRupees('0.05')).toEqual({ ok: true, paise: 5 });
  expect(parseRupees('9,00,71,99,25,47,409.91')).toEqual({ ok: true, paise: Number.MAX_SAFE_INTEGER });
});

test('parseRupees rejects precision, sign, junk and unsafe amounts', () => {
  expect(parseRupees('4.255')).toEqual({ ok: false, reason: 'precision' });
  expect(parseRupees('-1')).toEqual({ ok: false, reason: 'negative' });
  expect(parseRupees('')).toEqual({ ok: false, reason: 'empty' });
  expect(parseRupees('twelve')).toEqual({ ok: false, reason: 'invalid' });
  expect(parseRupees('1e5')).toEqual({ ok: false, reason: 'invalid' });
  expect(parseRupees('9,00,71,99,25,47,409.92')).toEqual({ ok: false, reason: 'unsafe' });
});
