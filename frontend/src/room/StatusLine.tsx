// Room status (brief 9.3): a connection state, never "Live"; the participant count; "Use this
// tab" when another tab of this browser holds the socket (section 13 item 3).
import { formatLocal } from '../lib/clock';
import { isStale, type RoomState } from '../live/room';
import { useStrings } from '../strings/catalogue';
import styles from './room.module.css';
import type { RoomSession } from './session';

export function StatusLine({
  state,
  session,
  signedIn,
  startTimeMs,
}: {
  state: RoomState;
  session: RoomSession | null;
  signedIn: boolean;
  startTimeMs: number | null;
}) {
  const s = useStrings().roomUi.status;
  const n = state.participants ?? 0;
  // the green dot means genuinely live; degraded or stale shows a hollow dot, the word carries the state
  const live = !isStale(state) && (state.connection === 'connected' || state.connection === 'reconnected') && state.subscribed;
  let text: string;
  let useTab = false;
  if (state.terminal !== null) return null; // closed: no connection to speak of, the price block says how it ended
  if (state.status === null) text = s.connecting;
  else if (state.status === 'draft') text = s.draft;
  else if (state.degraded) {
    const at = session?.getLastUpdateMs() ?? null;
    text = at === null ? s.reconnecting : s.lastUpdate(formatLocal(at, { seconds: true }));
  } else if (state.connection === 'reconnecting' || state.connection === 'offline') text = s.reconnecting;
  else if (state.status === 'scheduled' && startTimeMs !== null) text = s.opens(formatLocal(startTimeMs, { seconds: true }), n);
  else if (state.connection === 'polling') {
    if (signedIn && state.closedCode !== 4004) {
      text = s.otherTab;
      useTab = true;
    } else text = s.polling(n);
  } else text = s.connected(n);
  return (
    <div className={styles.status}>
      {state.status === 'draft' ? null : <i className={live ? styles.dotLive : styles.dot} aria-hidden="true" />}
      <span className={styles.grow}>{text}</span>
      {useTab ? (
        <button type="button" className={styles.link} onClick={() => session?.useThisTab()}>
          {s.useThisTab}
        </button>
      ) : null}
    </div>
  );
}
