// The hero under the headline (brief 8.1): one sentence, the actions, the board. There is no live
// hero lot (the hero loop is deferred): with demo mode on and a verified stress run, the board
// replays that run; with demo mode off or no run yet, the brief's empty state. "Watch a live
// auction" opens the newest live real lot, and is left out when there is none (slice 5 Q4).
import { Link } from 'react-router';

import { landingEnIN } from '../strings/landing-en-IN';
import type { LandingApi } from './api';
import { Board, EmptyBoard } from './Board';
import boardStyles from './board.module.css';
import styles from './landing.module.css';
import type { LiveLot, OverviewState } from './useLanding';

const t = landingEnIN.hero;

export function Hero({ demo, overview, live, signedIn }: { demo: LandingApi; overview: OverviewState; live: LiveLot; signedIn: boolean }) {
  const replayId = overview.kind === 'on' ? overview.overview.last_closed_auction_id : null;
  const known = overview.kind !== 'loading';
  const watch = live.kind === 'done' ? live.lot : null;
  return (
    <>
      <p className={styles.lede}>{known ? (replayId === null ? t.ledeEmpty : t.ledeReplay) : null}</p>
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
      {!known ? <div className={boardStyles.band} /> : replayId === null ? <EmptyBoard /> : <Board demo={demo} auctionId={replayId} />}
    </>
  );
}
