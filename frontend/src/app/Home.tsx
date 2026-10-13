// The placeholder home route: proves the plumbing. The landing (brief 8) is a later slice.
import { useStrings } from '../strings/catalogue';
import styles from './Home.module.css';

export function Home() {
  const s = useStrings();
  return (
    <section>
      <h1 className={styles.title}>{s.home.title}</h1>
      <p className={styles.lede}>{s.home.lede}</p>
    </section>
  );
}
