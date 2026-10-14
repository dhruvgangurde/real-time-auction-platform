// FR-74: the email switches are sent as a full map, every type the backend knows (a missing type
// would keep its old value; an unknown one is refused by the server).
import { expect, test } from 'vitest';

import { TYPES } from './rows';
import { fullMap } from './preferences';

test('ticked types are on, every other known type is off, nothing else is sent', () => {
  const map = fullMap(['outbid', 'payment_due', 'made_up']);
  expect(Object.keys(map)).toEqual([...TYPES]);
  expect(map.outbid).toBe(true);
  expect(map.payment_due).toBe(true);
  expect(map.ending_soon).toBe(false);
  expect(map).not.toHaveProperty('made_up');
});
