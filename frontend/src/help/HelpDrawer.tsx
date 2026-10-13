// The room's help drawer (UD-4): a native non-modal <dialog> as a side panel, so the room keeps
// updating behind it; Escape closes; focus returns to the control that opened it (brief 11.4).
import { useEffect, useRef } from 'react';

import { useStrings, type HelpTopic } from '../strings/catalogue';
import { Topic } from './topics';
import styles from './topics.module.css';

export function HelpDrawer({ topic, onClose }: { topic: HelpTopic | null; onClose: () => void }) {
  const s = useStrings();
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current;
    if (dialog === null) return;
    if (topic === null) {
      if (dialog.open) dialog.close();
      return;
    }
    const opener = document.activeElement as HTMLElement | null;
    if (!dialog.open) dialog.show();
    dialog.querySelector<HTMLElement>('button')?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    dialog.addEventListener('keydown', onKey);
    return () => {
      dialog.removeEventListener('keydown', onKey);
      opener?.focus();
    };
  }, [topic, onClose]);
  const title = topic === null ? '' : s.help.topics[topic].title;
  return (
    <dialog ref={ref} className={styles.drawer} aria-label={s.help.openLabel(title)} onClose={onClose}>
      <div className={styles.drawerHead}>
        <button type="button" className={styles.closeButton} onClick={onClose}>
          {s.help.close}
        </button>
      </div>
      {topic === null ? null : <Topic topic={topic} />}
    </dialog>
  );
}
