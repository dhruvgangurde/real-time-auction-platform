// UD-4 (brief 9.11): four topics, each with a worked example in rupees and a described diagram.
import { expect, test } from 'vitest';

import { HELP_TOPICS } from '../strings/catalogue';
import { helpEnIN } from '../strings/help-en-IN';

test('every topic has an example in rupees, a diagram description and its labels', () => {
  expect(HELP_TOPICS).toEqual(['reserve', 'increment', 'maximum', 'clock']);
  for (const topic of HELP_TOPICS) {
    const t = helpEnIN.topics[topic];
    expect(t.example).toContain('₹');
    expect(t.body.length).toBeGreaterThanOrEqual(2);
    expect(t.diagram.length).toBeGreaterThan(20);
    expect(t.labels.length).toBeGreaterThanOrEqual(4);
  }
  // the reserve topic never gives an amount for the reserve itself (BR-6)
  expect(helpEnIN.topics.reserve.example).not.toMatch(/reserve (of|is|at) ₹/i);
});
