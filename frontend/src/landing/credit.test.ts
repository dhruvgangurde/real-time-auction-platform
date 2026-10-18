// A demo lot's photo credit (brief 7.4: credit lines are shown under the image). Images carry no
// credit field yet (a later backend item, decisions.md); the runner and the seed write it as the
// description's last paragraph, starting "Photo" (workers/demo_runner.py, app/demo.py).
import { expect, test } from 'vitest';

import { photoCredit } from './credit';

test("the description's last paragraph is the credit when it starts with Photo", () => {
  const runner =
    'A demo listing: the bidders are simulated and nothing is sold. Fifty demo bidders bid on it at once.\n\n' +
    'Photo of a comparable piece: Cleveland Museum of Art, 2020.206, CC0.';
  expect(photoCredit(runner)).toBe('Photo of a comparable piece: Cleveland Museum of Art, 2020.206, CC0.');
  expect(photoCredit('Model M4400, boots and runs.\n\nPhoto: Danamania, Wikimedia Commons, CC0.\n')).toBe(
    'Photo: Danamania, Wikimedia Commons, CC0.',
  );
});

test('no credit is made up: anything else is no credit', () => {
  expect(photoCredit('A bidri box with silver inlay.')).toBeNull();
  expect(photoCredit('Photo of the box.\n\nShips in a tube.')).toBeNull(); // not the last paragraph
  expect(photoCredit('')).toBeNull();
  expect(photoCredit(`Intro.\n\nPhoto: ${'x'.repeat(300)}`)).toBeNull(); // not a credit line
  expect(photoCredit('Intro.\n\nPhotography lessons included.')).toBeNull();
});
