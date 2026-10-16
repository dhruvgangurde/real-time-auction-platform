// What the server would refuse about a photo (FR-13, NFR-24, D10; services/images.py, config.py),
// said before the upload as the same error code. The server still decides: it decodes the file.
import type { ErrorCode } from '../strings/catalogue';

export const MAX_BYTES = 5 * 1024 * 1024;
export const MAX_IMAGES = 8;
export const TYPES = ['image/jpeg', 'image/png', 'image/webp'];

export function precheck(file: { type: string; size: number }, attached: number): ErrorCode | null {
  if (file.type !== '' && !TYPES.includes(file.type)) return 'UNSUPPORTED_IMAGE';
  if (file.size > MAX_BYTES) return 'IMAGE_TOO_LARGE';
  if (attached >= MAX_IMAGES) return 'TOO_MANY_IMAGES';
  return null;
}

/** A file on its way: a tile with its local preview until it is a stored photo, or dismissed. */
export type Upload = { key: string; file: File; preview: string; state: 'waiting' | 'uploading' | 'failed'; code: string | null };
export type UploadEvent =
  | { type: 'add'; uploads: Upload[] }
  | { type: 'uploading' | 'done' | 'dismiss'; key: string }
  | { type: 'failed'; key: string; code: string };

export function uploads(list: Upload[], e: UploadEvent): Upload[] {
  switch (e.type) {
    case 'add':
      return [...list, ...e.uploads];
    case 'uploading':
      return list.map((u) => (u.key === e.key ? { ...u, state: 'uploading' } : u));
    case 'failed':
      return list.map((u) => (u.key === e.key ? { ...u, state: 'failed', code: e.code } : u));
    case 'done':
      return list.filter((u) => u.key !== e.key);
    case 'dismiss': // only a refused one: a file still going cannot be dropped halfway
      return list.filter((u) => u.key !== e.key || u.state !== 'failed');
  }
}
