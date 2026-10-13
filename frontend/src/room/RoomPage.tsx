// The auction room, /auctions/:id (UI-3, brief 9.3): the lot loads once; the live state comes
// from one room session (the socket when this tab owns it, else the 2 s poll) and each region
// reads only its slice. The bid and proxy controls, the seller and admin panels sit in the live
// column between the countdown and the ledger (S6, S7).
import { useQuery } from '@tanstack/react-query';
import { useCallback, useEffect, useState } from 'react';
import { Link, useParams } from 'react-router';

import { ApiError } from '../api/client';
import { useApp, useToken } from '../app/context';
import { NotFound } from '../app/NotFound';
import { HelpDrawer } from '../help/HelpDrawer';
import { errorMessage, useStrings, type HelpTopic } from '../strings/catalogue';
import { Banner } from './Banner';
import { Countdown } from './Countdown';
import { Ledger } from './Ledger';
import { Lot } from './Lot';
import { PriceBlock } from './PriceBlock';
import styles from './room.module.css';
import { StatusLine } from './StatusLine';
import { useRoom } from './useRoom';

// the phone header's thumbnail repeats the title beside it, so it is decorative (empty alt)
const DECORATIVE = '';

export function RoomPage() {
  const { id = '' } = useParams();
  const s = useStrings();
  const { api } = useApp();
  const token = useToken();
  const detail = useQuery({ queryKey: ['auction', id, token], queryFn: () => api.detail(id), retry: false });
  const me = useQuery({ queryKey: ['me', token], queryFn: () => api.me(), enabled: token !== null, staleTime: 60_000 });
  const { state, session } = useRoom(id, !detail.isError);
  const [helpTopic, setHelpTopic] = useState<HelpTopic | null>(null);
  const closeHelp = useCallback(() => setHelpTopic(null), []);
  const d = detail.data;
  const isSeller = d !== undefined && me.data !== undefined && d.seller.id === me.data.id;
  useEffect(() => {
    session?.setSeller(isSeller);
  }, [session, isSeller]);
  useEffect(() => {
    if (d !== undefined) session?.setWindowMs(d.antisnipe_threshold_s * 1000);
  }, [session, d]);

  if (detail.error instanceof ApiError) {
    if (detail.error.code === 'NOT_FOUND') return <NotFound />;
    return <p className={styles.hint}>{errorMessage(s, detail.error.code)}</p>;
  }
  if (d === undefined) return null;
  const thumb = d.images[0];
  return (
    <>
      <ol className={styles.crumbs} aria-label={s.roomUi.labels.crumbs}>
        <li>
          <Link to="/auctions">{s.nav.live}</Link>
        </li>
        {d.category === null ? null : <li>{d.category.name}</li>}
      </ol>
      <Banner state={state} startTimeMs={d.start_time_ms} />
      <div className={styles.room}>
        <section className={styles.live} aria-label={s.roomUi.labels.live}>
          <StatusLine state={state} session={session} signedIn={token !== null} startTimeMs={d.start_time_ms} />
          <div className={styles.mHead}>
            {thumb === undefined ? null : (
              <div className={styles.thumb}>
                <img src={thumb.url} alt={DECORATIVE} />
              </div>
            )}
            <div className={styles.mTitle}>{d.title}</div>
          </div>
          <PriceBlock state={state} startTimeMs={d.start_time_ms} onHelp={setHelpTopic} />
          <Countdown
            state={state}
            session={session}
            startTimeMs={d.start_time_ms}
            windowMs={d.antisnipe_threshold_s * 1000}
            extensionMs={d.antisnipe_extension_s * 1000}
          />
          <Ledger state={state} />
        </section>
        <Lot detail={d} onHelp={setHelpTopic} />
      </div>
      <HelpDrawer topic={helpTopic} onClose={closeHelp} />
    </>
  );
}

export const Component = RoomPage;
