// The payment page, /auctions/:id/pay (brief 9.5): its own document with its own CSP, the only one
// that allows Razorpay (brief 13 item 25); no router, no socket. The flow comes with the screen.
import { useStrings } from '../strings/catalogue';
import { payEnIN as p } from '../strings/pay-en-IN';
import styles from './pay.module.css';

export function PayPage() {
  const s = useStrings();
  return (
    <>
      <header className={styles.header}>
        <div className={styles.wrap}>
          <a className={styles.mark} href="/">
            {s.app.name}
          </a>
        </div>
      </header>
      <main id="main" className={styles.main}>
        <h1 className={styles.title}>{p.title}</h1>
      </main>
    </>
  );
}
