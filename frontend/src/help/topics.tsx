// The four UD-4 topics (brief 9.11): each a diagram and a worked example in rupees, shared by the
// /help page and the room's help drawer. Diagram text comes from the catalogue's labels, so the
// JSX holds no literal.
import { HELP_TOPICS, useStrings, type HelpTopic } from '../strings/catalogue';
import styles from './topics.module.css';

export { HELP_TOPICS };
export type { HelpTopic };

const INK = 'var(--ink)';
const INK3 = 'var(--ink-3)';
const RULE = 'var(--rule-strong)';
const MARIGOLD = 'var(--marigold)';
const GREEN = 'var(--green)';
const FONT = 'Anek Latin, sans-serif';

function Diagram({ topic, labels, description }: { topic: HelpTopic; labels: string[]; description: string }) {
  const t = (x: number, y: number, i: number, extra: Record<string, string> = {}) => (
    <text x={x} y={y} fontFamily={FONT} fontSize="14" fill={INK3} {...extra}>
      {labels[i]}
    </text>
  );
  switch (topic) {
    case 'reserve':
      return (
        <svg className={styles.diagram} viewBox="0 0 360 90" role="img" aria-label={description}>
          <line x1="20" y1="50" x2="340" y2="50" stroke={INK} strokeWidth="2" />
          <rect x="190" y="38" width="150" height="24" fill="var(--green-wash)" />
          <line x1="190" y1="26" x2="190" y2="74" stroke={INK} strokeWidth="1.5" strokeDasharray="4 3" />
          {t(20, 80, 0)}
          {t(340, 80, 1, { textAnchor: 'end' })}
          {t(30, 32, 2)}
          {t(200, 32, 3, { fill: GREEN })}
          {t(190, 18, 4, { textAnchor: 'middle', fill: INK })}
        </svg>
      );
    case 'increment':
      return (
        <svg className={styles.diagram} viewBox="0 0 360 90" role="img" aria-label={description}>
          <path d="M20 70 H120 V50 H220 V30 H320" fill="none" stroke={INK} strokeWidth="2" />
          <rect x="120" y="50" width="100" height="20" fill={MARIGOLD} opacity="0.5" />
          {t(20, 86, 0)}
          {t(120, 46, 1)}
          {t(220, 26, 2)}
          {t(130, 66, 3, { fill: INK })}
        </svg>
      );
    case 'maximum':
      return (
        <svg className={styles.diagram} viewBox="0 0 360 90" role="img" aria-label={description}>
          <line x1="20" y1="22" x2="340" y2="22" stroke={INK} strokeWidth="1.5" strokeDasharray="4 3" />
          <rect x="40" y="56" width="60" height="22" fill={RULE} />
          <rect x="120" y="44" width="60" height="34" fill={MARIGOLD} />
          {t(40, 52, 0)}
          {t(120, 40, 1, { fill: INK })}
          {t(340, 18, 2, { textAnchor: 'end', fill: INK })}
          {t(20, 18, 3, { fill: INK })}
        </svg>
      );
    case 'clock':
      return (
        <svg className={styles.diagram} viewBox="0 0 360 90" role="img" aria-label={description}>
          <defs>
            <pattern id="help-hatch" width="7" height="7" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
              <line x1="0" y1="0" x2="0" y2="7" stroke={RULE} strokeWidth="2.5" />
            </pattern>
          </defs>
          <rect x="180" y="40" width="80" height="20" fill="url(#help-hatch)" />
          <rect x="260" y="44" width="80" height="12" fill={MARIGOLD} />
          <line x1="20" y1="50" x2="340" y2="50" stroke={INK} strokeWidth="2" />
          <line x1="230" y1="34" x2="230" y2="66" stroke={INK} strokeWidth="3" />
          {t(230, 26, 0, { textAnchor: 'middle', fill: INK })}
          {t(260, 80, 1, { textAnchor: 'middle' })}
          {t(340, 80, 2, { textAnchor: 'end' })}
          {t(180, 34, 3)}
          {t(300, 36, 4, { textAnchor: 'middle', fill: INK })}
        </svg>
      );
  }
}

export function Topic({ topic, heading = 'h2' }: { topic: HelpTopic; heading?: 'h1' | 'h2' }) {
  const s = useStrings();
  const t = s.help.topics[topic];
  const H = heading;
  return (
    <article className={styles.topic} id={`help-${topic}`}>
      <H className={styles.title}>{t.title}</H>
      {t.body.map((p) => (
        <p key={p} className={styles.body}>
          {p}
        </p>
      ))}
      <Diagram topic={topic} labels={t.labels} description={t.diagram} />
      <p className={styles.example}>{t.example}</p>
    </article>
  );
}
