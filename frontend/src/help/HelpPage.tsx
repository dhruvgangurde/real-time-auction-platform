// /help and /help/:topic (UD-4): the four topics and the API reference link. A header band, then
// the contents beside the topics (design/polish): on the whole page its links jump to each topic;
// on one topic's page they open the others.
import { Link, useParams } from 'react-router';

import { type HelpTopic } from '../strings/catalogue';
import { helpEnIN } from '../strings/help-en-IN';
import { HELP_TOPICS, Topic } from './topics';
import styles from './topics.module.css';

export function HelpPage() {
  const { topic } = useParams();
  const only = HELP_TOPICS.find((t) => t === topic);
  return (
    <section className={styles.page}>
      <header className={styles.top}>
        <p className={styles.eyebrow}>{helpEnIN.eyebrow}</p>
        <h1 className={styles.pageTitle}>{helpEnIN.title}</h1>
        <p className={styles.lede}>{helpEnIN.lede}</p>
      </header>
      <div className={styles.columns}>
        <nav className={styles.toc} aria-labelledby="help-contents">
          <p className={styles.tocTitle} id="help-contents">
            {helpEnIN.onThisPage}
          </p>
          <ul>
            {HELP_TOPICS.map((t) => (
              <li key={t}>
                {only ? (
                  <Link to={`/help/${t}`} aria-current={t === only ? 'page' : undefined}>
                    {helpEnIN.topics[t].question}
                  </Link>
                ) : (
                  <a href={`#help-${t}`}>{helpEnIN.topics[t].question}</a>
                )}
              </li>
            ))}
          </ul>
        </nav>
        <div>
          {(only ? [only] : HELP_TOPICS).map((t: HelpTopic) => (
            <Topic key={t} topic={t} heading="h2" />
          ))}
        </div>
      </div>
    </section>
  );
}

export const Component = HelpPage;
