// /help and /help/:topic (UD-4): the four topics and the API reference link.
import { Link, useParams } from 'react-router';

import { useStrings, type HelpTopic } from '../strings/catalogue';
import { HELP_TOPICS, Topic } from './topics';
import styles from './topics.module.css';

export function HelpPage() {
  const s = useStrings();
  const { topic } = useParams();
  const only = HELP_TOPICS.find((t) => t === topic);
  return (
    <section className={styles.page}>
      <h1 className={styles.pageTitle}>{s.help.title}</h1>
      <p className={styles.lede}>{s.help.lede}</p>
      <ul className={styles.toc}>
        {HELP_TOPICS.map((t) => (
          <li key={t}>
            <Link to={`/help/${t}`}>{s.help.topics[t].question}</Link>
          </li>
        ))}
        <li>
          <a href="/api/v1/docs">{s.help.apiDocs}</a>
        </li>
      </ul>
      {(only ? [only] : HELP_TOPICS).map((t: HelpTopic) => (
        <Topic key={t} topic={t} heading={only ? 'h2' : 'h2'} />
      ))}
    </section>
  );
}

export const Component = HelpPage;
