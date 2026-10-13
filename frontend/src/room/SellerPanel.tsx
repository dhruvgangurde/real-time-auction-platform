// The seller's own-auction panel (brief 9.3, 9.8, BR-6): the reserve amount, visible only to the
// seller, with whether it is currently met. No bid or proxy control (SELLER_CANNOT_BID).
import type { AuctionDetail } from '../lib/contract';
import { formatINR } from '../lib/money';
import type { RoomState } from '../live/room';
import { useStrings } from '../strings/catalogue';
import styles from './room.module.css';

export function SellerPanel({ detail, state }: { detail: AuctionDetail; state: RoomState }) {
  const s = useStrings().roomUi.seller;
  const reserve = detail.reserve_price;
  return (
    <div className={`${styles.panel} ${styles.proxyPanel}`}>
      <h2>{s.heading}</h2>
      {typeof reserve === 'number' ? (
        <>
          <div className={styles.own}>
            <span className={`${styles.v} tabular`}>{formatINR(reserve)}</span>
            <span className={styles.only}>{s.reserveBadge}</span>
          </div>
          <p className={styles.privacy}>{state.reserveMet ? s.met : s.notMet}</p>
        </>
      ) : null}
      <p className={styles.hint}>{s.cantBid}</p>
    </div>
  );
}
