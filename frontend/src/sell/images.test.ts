// A photo is checked before it is sent (FR-13, NFR-24, D10): JPEG, PNG or WebP, at most 5 MB, at
// most 8 per listing. The answer is the server's own error code, so the message is the same one
// the server's refusal would show; the server still decides (a file with no type goes up).
import { expect, test } from 'vitest';

import { MAX_BYTES, MAX_IMAGES, precheck } from './images';

const file = (type: string, size: number) => ({ type, size });

test('the three types, up to 5 MB, while fewer than 8 are attached', () => {
  for (const type of ['image/jpeg', 'image/png', 'image/webp']) expect(precheck(file(type, MAX_BYTES), 0)).toBeNull();
  expect(precheck(file('image/gif', 10), 0)).toBe('UNSUPPORTED_IMAGE');
  expect(precheck(file('application/pdf', 10), 0)).toBe('UNSUPPORTED_IMAGE');
  expect(precheck(file('image/jpeg', MAX_BYTES + 1), 0)).toBe('IMAGE_TOO_LARGE');
  expect(precheck(file('image/jpeg', 10), MAX_IMAGES - 1)).toBeNull();
  expect(precheck(file('image/jpeg', 10), MAX_IMAGES)).toBe('TOO_MANY_IMAGES');
  expect(precheck(file('', 10), 0)).toBeNull(); // no type known here: the server decodes it
  expect([MAX_BYTES, MAX_IMAGES]).toEqual([5 * 1024 * 1024, 8]);
});
