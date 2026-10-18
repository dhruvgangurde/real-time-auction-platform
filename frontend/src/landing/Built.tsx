// Brief 8.6 "How it's built" and 8.7 the footer. The architecture drawing is inline SVG (two
// layouts, one shown per viewport), hidden from assistive technology because a text list says the
// same; the serialisation point (Redis) has the heavier border, marigold stays for live moments.
// The seven decisions link to docs/architecture.md and docs/decisions.md on GitHub (VITE_REPO_URL).
import { Link } from 'react-router';

import { landingEnIN } from '../strings/landing-en-IN';
import styles from './landing.module.css';
import { repo } from './repo';

const t = landingEnIN.built;
const a = t.drawing;

function Box({ x, y, w, h, title, lines, strong = false }: { x: number; y: number; w: number; h: number; title: string; lines: readonly string[]; strong?: boolean }) {
  return (
    <g>
      <rect x={x} y={y} width={w} height={h} fill="var(--paper)" stroke="var(--ink)" strokeWidth={strong ? 5 : 2} />
      <text x={x + 12} y={y + 25} fontSize="18" fontWeight="600" fill="var(--ink)">
        {title}
      </text>
      {lines.map((line, i) => (
        <text key={line} x={x + 12} y={y + 48 + i * 20} fontSize="16" fill="var(--ink-2)">
          {line}
        </text>
      ))}
    </g>
  );
}

const Line = ({ d }: { d: string }) => <path d={d} fill="none" stroke="var(--ink)" strokeWidth="1.5" />;

function Label({ x, y, text, anchor }: { x: number; y: number; text: string; anchor?: 'end' | 'middle' }) {
  return (
    <text x={x} y={y} fontSize="16" fill="var(--ink-3)" textAnchor={anchor}>
      {text}
    </text>
  );
}

function Desktop() {
  return (
    <svg className={styles.archD} viewBox="0 0 780 500" fontFamily="var(--font-ui)" aria-hidden="true">
      <Box x={16} y={16} w={170} h={70} title={a.browser} lines={[a.browserSub]} />
      <Box x={256} y={16} w={240} h={70} title={a.api1} lines={[a.apiSub]} />
      <Box x={256} y={106} w={240} h={70} title={a.api2} lines={[a.apiSub]} />
      <Box x={570} y={16} w={194} h={92} title={a.razorpay} lines={a.razorpaySub} />
      <Box x={256} y={226} w={240} h={112} title={a.redis} lines={a.redisSub} strong />
      <Box x={16} y={392} w={330} h={92} title={a.worker} lines={a.workerSub} />
      <Box x={434} y={392} w={330} h={92} title={a.postgres} lines={a.postgresSub} />
      <Line d="M186 51 H256" />
      <Line d="M186 64 H221 V141 H256" />
      <Line d="M496 51 H570" />
      <Line d="M376 86 V106" />
      <Line d="M376 176 V226" />
      <Line d="M300 338 V392" />
      <Line d="M452 338 V392" />
      <Line d="M346 438 H434" />
      <Line d="M496 141 H530 V392" />
      <Label x={366} y={206} text={a.oneAtATime} anchor="end" />
      <Label x={290} y={370} text={a.outbox} anchor="end" />
      <Label x={390} y={430} text={a.writes} anchor="middle" />
      <Label x={538} y={270} text={a.reads} />
    </svg>
  );
}

function Phone() {
  return (
    <svg className={styles.archM} viewBox="0 0 360 690" fontFamily="var(--font-ui)" aria-hidden="true">
      <Box x={16} y={16} w={328} h={66} title={a.browser} lines={[a.browserSub]} />
      <Box x={16} y={112} w={158} h={88} title={a.api1Short} lines={a.apiSubShort} />
      <Box x={186} y={112} w={158} h={88} title={a.api2Short} lines={a.apiSubShort} />
      <Box x={16} y={236} w={328} h={112} title={a.redis} lines={a.redisSubShort} strong />
      <Box x={16} y={384} w={328} h={92} title={a.worker} lines={a.workerSub} />
      <Box x={16} y={512} w={328} h={70} title={a.postgres} lines={[a.postgresShort]} />
      <Box x={16} y={612} w={328} h={66} title={a.razorpay} lines={[a.razorpayShort]} />
      <Line d="M180 82 V112" />
      <Line d="M95 200 V236" />
      <Line d="M265 200 V236" />
      <Line d="M180 348 V384" />
      <Line d="M180 476 V512" />
      <Line d="M344 156 H352 V645 H344" />
    </svg>
  );
}

export function Built() {
  const architecture = repo('docs/architecture.md');
  const decisions = repo('docs/decisions.md');
  return (
    <section className={styles.section} aria-labelledby="built-title">
      <p className={styles.eyebrow}>{t.eyebrow}</p>
      <h2 className={styles.sectionTitle} id="built-title">
        {t.title}
      </h2>
      <div className={styles.built}>
        <figure className={styles.arch}>
          <Desktop />
          <Phone />
          <figcaption className="sr-only">
            <ul>
              {t.equivalent.map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
          </figcaption>
        </figure>
        <div>
          <ul className={styles.decisions}>
            {t.decisions.map(([decision, refs]) => (
              <li key={decision}>
                {decision}
                <span>{refs}</span>
              </li>
            ))}
          </ul>
          {architecture === null || decisions === null ? null : (
            <div className={styles.links}>
              <a className={styles.link} href={architecture}>
                {t.architecture}
              </a>
              <a className={styles.link} href={decisions}>
                {t.decisionsLink}
              </a>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}

export function Footer() {
  const source = repo('');
  return (
    <footer className={styles.footer}>
      <p>{t.footer.testMode}</p>
      <p>{t.footer.demo}</p>
      <div className={styles.footerLinks}>
        <Link className={styles.link} to="/help">
          {t.footer.help}
        </Link>
        {source === null ? null : (
          <a className={styles.link} href={source}>
            {t.footer.source}
          </a>
        )}
      </div>
    </footer>
  );
}
