// The landing at / (brief 8). The h1 is in the entry so it paints with the shell and is the LCP
// element (8.1 Performance); everything under it is the landing chunk, loaded lazily, so the entry
// never carries the landing's code or strings (src/app/chunks.test.ts).
import { lazy, Suspense } from 'react';

import { useStrings } from '../strings/catalogue';
import styles from './Home.module.css';

const LandingBody = lazy(() => import('../landing/LandingBody'));

export function Home() {
  const [first, second, third] = useStrings().home.lines;
  // one grid for the page: the headline and the hero's text in the first column, the hero's card
  // in the second (landing/board.module.css), every section under them across both
  return (
    <div className={styles.page}>
      <h1 className={styles.title} id="hero-title">
        {/* line breaks, not block spans: the h1 stays one text block, so it is the one LCP candidate */}
        <span>{first}</span>
        <br /> <span>{second}</span>
        <br /> <span>{third}</span>
      </h1>
      <Suspense fallback={<div className={styles.reserve} />}>
        <LandingBody />
      </Suspense>
    </div>
  );
}
