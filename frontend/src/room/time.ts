// Pure helpers behind the countdown and the timeline (UI-4, brief 11.4): the milestones a
// countdown crosses, the geometry of the now-to-end ruler (design/mockups/room.html) and the
// first changed character of a price for the tick highlight (brief 6).

/** The polite milestones, largest first: 5 min, 1 min, 30 s, 10 s. */
export const MILESTONES_MS = [300_000, 60_000, 30_000, 10_000] as const;

/** The milestones crossed between two readings of the remaining time (same end time). */
export function crossed(prevRemainingMs: number, nextRemainingMs: number): number[] {
  return MILESTONES_MS.filter((t) => prevRemainingMs > t && nextRemainingMs <= t);
}

/** The index of the first character where two formatted prices differ (prev.length when none). */
export function changedFrom(prev: string, next: string): number {
  let i = 0;
  while (i < prev.length && i < next.length && prev[i] === next[i]) i += 1;
  return i;
}

export type Ruler = {
  width: number;
  ticks: { x: number; long: boolean; onBand: boolean }[];
  /** the soft-close window before the end, hatched; none once nothing can extend (FR-54) */
  zone: { x0: number; x1: number } | null;
  /** the time the latest extension added, in marigold */
  band: { x0: number; x1: number } | null;
  /** the end before that extension, marked "was" */
  was: { x: number; anchorEnd: boolean } | null;
};

const STEPS_S = [10, 30, 60, 300, 600];

/** The ruler always runs from now (left edge) to the current end (right edge). */
export function rulerModel(
  nowMs: number,
  endMs: number,
  oldEndMs: number | null,
  windowMs: number,
  capped: boolean,
  width: number,
): Ruler {
  const span = Math.max(1_000, endMs - nowMs);
  const x = (t: number) => (Math.min(span, Math.max(0, t - nowMs)) / span) * (width - 2) + 1;
  // tick marks at least 12 px apart
  const step = (STEPS_S.find((s) => span / 1000 / s <= width / 12) ?? 600) * 1000;
  const major = step < 60_000 ? 60_000 : step < 300_000 ? 300_000 : 1_800_000;
  const ticks: Ruler['ticks'] = [];
  for (let t = Math.ceil(nowMs / step) * step; t <= endMs; t += step) {
    ticks.push({ x: x(t), long: t % major === 0, onBand: oldEndMs !== null && t >= oldEndMs });
  }
  const zone = capped ? null : { x0: x(endMs - windowMs), x1: x(endMs) };
  const band = oldEndMs !== null ? { x0: x(oldEndMs), x1: x(endMs) } : null;
  const was = oldEndMs !== null ? { x: x(oldEndMs), anchorEnd: x(oldEndMs) > 100 } : null;
  return { width, ticks, zone, band, was };
}
