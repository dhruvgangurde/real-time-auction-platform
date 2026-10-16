// What an administrator may do from the room (FR-63, BR-9; admin.py, services/admin.py): force-close
// a live or closing auction, cancel a draft, scheduled or live one; nothing on a finished one.
import { expect, test } from 'vitest';

import { roomActions } from './actions';

test('force-close while bidding runs or closes; cancel before or during bidding; nothing after', () => {
  expect(roomActions('draft')).toEqual({ forceClose: false, cancel: true });
  expect(roomActions('scheduled')).toEqual({ forceClose: false, cancel: true });
  expect(roomActions('live')).toEqual({ forceClose: true, cancel: true });
  expect(roomActions('closing')).toEqual({ forceClose: true, cancel: false });
  for (const status of ['closed_sold', 'closed_no_sale', 'cancelled'] as const) expect(roomActions(status)).toEqual({ forceClose: false, cancel: false });
});
