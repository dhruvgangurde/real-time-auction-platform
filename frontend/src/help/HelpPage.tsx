// /help and /help/:topic (UD-4): the four topics and the API reference link.
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
      <h1 className={styles.pageTitle}>{helpEnIN.title}</h1>
      <p className={styles.lede}>{helpEnIN.lede}</p>
      <ul className={styles.toc}>
        {HELP_TOPICS.map((t) => (
          <li key={t}>
            <Link to={`/help/${t}`}>{helpEnIN.topics[t].question}</Link>
          </li>
        ))}
      </ul>
      {(only ? [only] : HELP_TOPICS).map((t: HelpTopic) => (
        <Topic key={t} topic={t} heading={only ? 'h2' : 'h2'} />
      ))}
    </section>
  );
}

export const Component = HelpPage;
