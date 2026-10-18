// The hero beside and under the headline (brief 8.1, design/polish): one sentence, the actions,
// the ticks in the headline's column; the card in the other (app/Home.module.css lays out the grid). There is no live
// hero lot (the hero loop is deferred): with demo mode on and a verified stress run, the board
// replays that run; before any run is verified, a run in progress is said so (heroLede.ts); with
// demo mode off or no run yet, the brief's empty state. "Watch a live
// auction" opens the newest live real lot, and is left out when there is none (slice 5 Q4).
import { Link } from 'react-router';

import { landingEnIN } from '../strings/landing-en-IN';
import type { LandingApi } from './api';
import { emptyBoardTitle, heroLede } from './heroLede';
import { Board, EmptyBoard } from './Board';
import boardStyles from './board.module.css';
import styles from './landing.module.css';
import type { LiveLot, OverviewState } from './useLanding';

const t = landingEnIN.hero;

export function Hero({
  demo,
  overview,
  watching,
  live,
  signedIn,
}: {
  demo: LandingApi;
  overview: OverviewState;
  watching: boolean;
  live: LiveLot;
  signedIn: boolean;
}) {
  const replayId = overview.kind === 'on' ? overview.overview.last_closed_auction_id : null;
  const known = overview.kind !== 'loading';
  const watch = live.kind === 'done' ? live.lot : null;
  const state = heroLede(overview, watching);
  const lede = { pending: null, empty: t.ledeEmpty, running: t.ledeRunning, replay: t.ledeReplay }[state];
  return (
    <>
      <p className={styles.lede}>{lede}</p>
      <div className={styles.actions}>
        {live.kind === 'done' ? (
          <>
            {watch === null ? null : (
              <Link className={styles.primary} to={`/auctions/${watch.id}`}>
                {t.watch}
              </Link>
            )}
            <Link className={watch === null ? styles.primary : styles.quiet} to={signedIn ? '/auctions' : '/register'}>
              {signedIn ? t.browse : t.start}
            </Link>
          </>
        ) : null}
      </div>
      <ul className={styles.ticks}>
        {t.ticks.map((tick) => (
          <li key={tick}>
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M20 6 9 17l-5-5" />
            </svg>
            {tick}
          </li>
        ))}
      </ul>
      {!known ? <div className={boardStyles.card} /> : replayId === null ? <EmptyBoard title={emptyBoardTitle(state)} /> : <Board demo={demo} auctionId={replayId} />}
    </>
  );
}
