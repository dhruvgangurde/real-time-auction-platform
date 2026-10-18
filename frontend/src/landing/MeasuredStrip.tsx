// Brief 8.4: the numbers strip, from the committed load-test results (measured.ts), read at build
// time. Each value with its target, the date, the machine and the stack, and links to the results
// and the k6 scripts. Not rendered at all when the figures cannot be read exactly.
import { landingEnIN } from '../strings/landing-en-IN';
import styles from './landing.module.css';
import { extract, latestResults } from './measured';
import { repo } from './repo';

const t = landingEnIN.measured;
const FILE = latestResults();
const MEASURED = FILE === null ? null : extract(FILE);
const day = (date: string) =>
  new Intl.DateTimeFormat('en-IN', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' }).format(new Date(`${date}T00:00:00Z`));

export function MeasuredStrip() {
  if (MEASURED === null) return null;
  const { figures, machine, date } = MEASURED;
  const results = repo('loadtest/RESULTS.md');
  const scripts = repo('loadtest/room.js');
  return (
    <section className={styles.section} aria-labelledby="measured-title">
      <p className={styles.eyebrow}>{t.eyebrow}</p>
      <h2 className={styles.sectionTitle} id="measured-title">
        {t.title}
      </h2>
      <ul className={styles.strip}>
        {figures.map((f) => {
          const detail = f.detail === null ? null : (t.details as Record<string, ((n: string) => string) | undefined>)[f.id]?.(f.detail);
          return (
            <li key={f.id} className={styles.figure}>
              <span className={`${styles.figureValue} tabular`}>{f.value}</span>
              <span className={styles.figureLabel}>{t.labels[f.id as keyof typeof t.labels]}</span>
              <span className={styles.figureTarget}>{t.targets[f.id as keyof typeof t.targets]}</span>
              {detail ? <span className={styles.figureTarget}>{detail}</span> : null}
              <span className={styles.figureId}>{f.id}</span>
            </li>
          );
        })}
      </ul>
      <p className={styles.conditions}>
        {t.when(day(date), machine.k6)} {t.machine(machine.cpu, machine.threads, machine.ramGib, machine.dockerCpus, machine.dockerGib)}
      </p>
      {results === null || scripts === null ? null : (
        <div className={styles.links}>
          <a className={styles.link} href={results}>
            {t.results}
          </a>
          <a className={styles.link} href={scripts}>
            {t.scripts}
          </a>
        </div>
      )}
    </section>
  );
}
