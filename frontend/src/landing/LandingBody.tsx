// The landing under its headline (brief 8): the hero's sentence and actions, then the sections.
// Loaded lazily by app/Home.tsx: this chunk holds the landing's code and strings.
import { Link } from 'react-router';

import { landingEnIN as t } from '../strings/landing-en-IN';
import styles from './landing.module.css';

export default function LandingBody() {
  return (
    <>
      <p className={styles.lede}>{t.hero.ledeEmpty}</p>
      <div className={styles.actions} role="group" aria-label={t.hero.actions}>
        <Link className={styles.primary} to="/register">
          {t.hero.start}
        </Link>
      </div>
    </>
  );
}
