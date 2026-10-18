// Which sentence the hero says under the headline (brief 8.1): the replay of the latest verified
// run; before any run is verified, a run in progress (its replay comes once it is checked): one
// GET /demo names, or one "Try to break it" is watching, started here after the page loaded; else
// the empty state. Nothing until GET /demo has answered.
import type { OverviewState } from './useLanding';

export type HeroLede = 'pending' | 'empty' | 'running' | 'replay';

export function heroLede(overview: OverviewState, watching: boolean): HeroLede {
  if (overview.kind === 'loading') return 'pending';
  if (overview.kind !== 'on') return 'empty';
  const { last_closed_auction_id: replay, stress } = overview.overview;
  if (replay !== null) return 'replay';
  return stress.active_run_id !== null || watching ? 'running' : 'empty';
}
