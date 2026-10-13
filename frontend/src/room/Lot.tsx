// The lot column (brief 9.3): photographs on plates, title, description, the listing's facts
// (times in the viewer's zone, the reserve as a fact, the amount for its owner only, BR-6) and
// the four help questions, which open the drawer (UD-4).
import { formatDay, formatLocal, formatRemaining, formatWindow } from '../lib/clock';
import type { AuctionDetail } from '../lib/contract';
import { formatINR } from '../lib/money';
import { HELP_TOPICS, useStrings, type HelpTopic } from '../strings/catalogue';
import styles from './room.module.css';

export function Lot({ detail, onHelp }: { detail: AuctionDetail; onHelp: (topic: HelpTopic) => void }) {
  const s = useStrings();
  const l = s.roomUi.lot;
  const [first, ...rest] = [...detail.images].sort((a, b) => a.position - b.position);
  const time = (ms: number) => formatLocal(ms, { seconds: true });
  const alt = l.photoAlt(detail.title);
  const window = formatWindow(detail.antisnipe_threshold_s * 1000, s.roomUi.units);
  const extension = formatRemaining(detail.antisnipe_extension_s * 1000);
  const reserve =
    typeof detail.reserve_price === 'number' ? l.reserveOwn(formatINR(detail.reserve_price)) : detail.has_reserve ? l.reserveYes : l.reserveNo;
  return (
    <section className={styles.lot} aria-label={s.roomUi.labels.lot}>
      {first === undefined ? null : (
        <figure className={styles.figure}>
          <div className={styles.plate}>
            <img src={first.url} alt={alt} />
          </div>
        </figure>
      )}
      {rest.length === 0 ? null : (
        <div className={styles.thumbs}>
          {rest.map((img) => (
            <img key={img.id} src={img.url} alt={alt} />
          ))}
        </div>
      )}
      <h1 className={styles.title}>{detail.title}</h1>
      <p className={styles.desc}>{detail.description}</p>
      <dl className={styles.facts}>
        <div>
          <dt>{l.listing}</dt>
          <dd>{l.demo}</dd>
        </div>
        {detail.category === null ? null : (
          <div>
            <dt>{l.category}</dt>
            <dd>{detail.category.name}</dd>
          </div>
        )}
        {detail.seller.name === null ? null : (
          <div>
            <dt>{l.seller}</dt>
            <dd>{detail.seller.name}</dd>
          </div>
        )}
        <div>
          <dt>{l.bidding}</dt>
          <dd className="tabular">{l.window(`${formatDay(detail.start_time_ms)}, ${time(detail.start_time_ms)}`, time(detail.original_end_time_ms))}</dd>
        </div>
        <div>
          <dt>{l.startingBid}</dt>
          <dd className="tabular">{l.risingBy(formatINR(detail.start_price), formatINR(detail.min_increment))}</dd>
        </div>
        <div>
          <dt>{l.softClose}</dt>
          <dd>{l.softCloseText(window, extension)}</dd>
        </div>
        <div>
          <dt>{l.reserve}</dt>
          <dd>{reserve}</dd>
        </div>
      </dl>
      <div className={styles.help}>
        {HELP_TOPICS.map((t) => (
          <button key={t} type="button" className={styles.link} onClick={() => onHelp(t)}>
            {s.help.topics[t].question}
          </button>
        ))}
      </div>
    </section>
  );
}
