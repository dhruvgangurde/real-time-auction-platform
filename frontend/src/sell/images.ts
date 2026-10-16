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
