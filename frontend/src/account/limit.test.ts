// The title lookups of "Your bids" run at most four at a time (Q3 of the slice 4 plan): the rest
// wait their turn, every one finishes, and one that fails frees its place.
import { expect, test } from 'vitest';

import { limiter } from './limit';

test('never more than four at once; all of them finish; a failure frees its place', async () => {
  const run = limiter(4);
  let active = 0;
  let most = 0;
  const task = (i: number) => async () => {
    active += 1;
    most = Math.max(most, active);
    await new Promise((r) => setTimeout(r, 5));
    active -= 1;
    if (i === 3) throw new Error('lookup failed');
    return i;
  };
  const results = await Promise.allSettled(Array.from({ length: 20 }, (_, i) => run(task(i))));
  expect(most).toBe(4);
  expect(results.filter((r) => r.status === 'fulfilled')).toHaveLength(19);
  expect(results[3]?.status).toBe('rejected');
  expect(active).toBe(0);
});
