// Unknown routes (brief 9.1): say so and offer the way back.
import { Link } from 'react-router';

import { useStrings } from '../strings/catalogue';
import styles from './Home.module.css';

export function NotFound() {
  const s = useStrings();
  return (
    <section>
      <h1 className={styles.title}>{s.notFound.title}</h1>
      <p className={styles.lede}>{s.notFound.body}</p>
      <p>
        <Link to="/auctions">{s.notFound.back}</Link>
      </p>
    </section>
  );
}
