import { describe, expect, test } from 'vitest';

import { changedFrom, crossed, rulerModel } from './time';

describe('countdown milestones (brief 11.4)', () => {
  test('each milestone fires once, when the remaining time passes it', () => {
    expect(crossed(301_000, 299_000)).toEqual([300_000]);
    expect(crossed(300_000, 299_000)).toEqual([]); // already at it: not crossed again
    expect(crossed(61_000, 9_000)).toEqual([60_000, 30_000, 10_000]); // a long gap crosses several
    expect(crossed(5_000, 0)).toEqual([]);
  });
});

describe('price tick', () => {
  test('finds the first changed character of the formatted price', () => {
    expect(changedFrom('₹12,000', '₹12,500')).toBe(4);
    expect(changedFrom('₹9,500', '₹10,000')).toBe(1);
    expect(changedFrom('₹14,000', '₹14,000')).toBe(7);
  });
});

describe('the ruler (design/mockups/room.html)', () => {
  const now = 1_000_000_000;
  test('runs from now to the end with the last window hatched', () => {
    const r = rulerModel(now, now + 600_000, null, 60_000, false, 520);
    expect(r.ticks.length).toBeGreaterThan(5);
    expect(r.ticks.every((t) => t.x >= 1 && t.x <= 519)).toBe(true);
    expect(r.zone).toEqual({ x0: r.ticks.length > 0 ? expect.any(Number) : 0, x1: 519 });
    expect(r.zone!.x0).toBeCloseTo(1 + 518 * 0.9, 5);
    expect(r.band).toBeNull();
    expect(r.was).toBeNull();
  });
  test('an extension adds a marigold band from the old end and a "was" mark', () => {
    const r = rulerModel(now, now + 120_000, now + 60_000, 60_000, false, 520);
    expect(r.band).toEqual({ x0: 260, x1: 519 });
    expect(r.was).toEqual({ x: 260, anchorEnd: true });
    expect(r.ticks.filter((t) => t.onBand).every((t) => t.x >= 260)).toBe(true);
  });
  test('no hatch once nothing can extend (FR-54); the span never collapses below a second', () => {
    expect(rulerModel(now, now + 120_000, null, 60_000, true, 358).zone).toBeNull();
    expect(rulerModel(now, now - 5, null, 60_000, false, 358).ticks.length).toBeLessThanOrEqual(1);
  });
});
