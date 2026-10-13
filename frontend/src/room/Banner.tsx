// The room banner (brief 9.3): degraded, signed out elsewhere (4004), reconnecting, and
// "Bidding is open" after the server took a scheduled room live (never from the local clock).
import { useState } from 'react';

import { formatLocal } from '../lib/clock';
import type { RoomState } from '../live/room';
import { useStrings } from '../strings/catalogue';
import styles from './room.module.css';

export function Banner({ state, startTimeMs }: { state: RoomState; startTimeMs: number | null }) {
  const s = useStrings().roomUi.banner;
  const [opened, setOpened] = useState(false);
  const [prevStatus, setPrevStatus] = useState(state.status);
  if (prevStatus !== state.status) {
    setPrevStatus(state.status);
    if (prevStatus === 'scheduled' && state.status === 'live') setOpened(true);
  }
  const time = (ms: number) => formatLocal(ms, { seconds: true });
  let head: string;
  let detail: string;
  if (state.degraded) {
    head = s.degraded;
    detail = s.degradedDetail;
  } else if (state.closedCode === 4004) {
    head = s.signedOut;
    detail = s.signedOutDetail;
  } else if ((state.connection === 'reconnecting' || state.connection === 'offline') && state.status !== null) {
    head = s.reconnecting;
    detail = s.reconnectingDetail;
  } else if (opened && state.status === 'live' && state.sequence === 0 && startTimeMs !== null && state.endTimeMs !== null) {
    head = s.open;
    detail = s.openDetail(time(startTimeMs), time(state.endTimeMs));
  } else return null;
  return (
    <div className={styles.banner} role="status">
      <b>{head}</b>
      <span className={styles.grow}>{detail}</span>
    </div>
  );
}
