// The marigold band (brief 6): one per new bid. It starts only when a sequence higher than the last
// one this page saw arrives while the room is live. Staleness, a pending or refused bid and a
// re-render never restart it, and the first snapshot (a fresh page load) never starts one. The
// ledger row and the price digits both key their band on the sequence this returns.
import { useState } from 'react';

import { isStale, type RoomState } from '../live/room';

/** seen: the highest sequence this page has seen; seq: the sequence whose band runs (or ran). */
export type Band = { seen: number | null; seq: number | null };
export const NO_BAND: Band = { seen: null, seq: null };

export function nextBand(band: Band, state: RoomState): Band {
  if (state.status === null) return band; // no snapshot yet
  if (band.seen === null) return { seen: state.sequence, seq: null }; // a fresh load: no band
  if (state.sequence <= band.seen) return band; // same sequence: stale, pending, refused, re-render
  const live = !isStale(state) && state.terminal === null;
  return { seen: state.sequence, seq: live ? state.sequence : band.seq };
}

/** The sequence whose band this component shows, held across renders. */
export function useBand(state: RoomState): number | null {
  const [band, setBand] = useState(NO_BAND);
  const next = nextBand(band, state);
  if (next !== band) setBand(next);
  return next.seq;
}
