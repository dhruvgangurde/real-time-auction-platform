// The email switches per notification type (FR-72, FR-74), sent as one full map of the known types.
import type { EmailPreferences } from '../lib/contract';
import { TYPES } from './rows';

export function fullMap(ticked: readonly string[]): EmailPreferences {
  return Object.fromEntries(TYPES.map((t) => [t, ticked.includes(t)]));
}
