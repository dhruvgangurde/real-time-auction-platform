// A native modal <dialog> for an administrative confirmation (UI-8): a required reason, the
// confirm disabled until it is typed, Escape and a quiet "keep it open" to dismiss, and focus
// returned to the control that opened it. The caller runs the action with the reason.
import { useEffect, useRef, useState } from 'react';

import { useStrings } from '../strings/catalogue';
import styles from './room.module.css';

export function ConfirmDialog({
  title,
  body,
  confirmLabel,
  onConfirm,
  onClose,
  error,
}: {
  title: string;
  body: React.ReactNode;
  confirmLabel: string;
  onConfirm: (reason: string) => void;
  onClose: () => void;
  error: string | null;
}) {
  const a = useStrings().roomUi.admin;
  const ref = useRef<HTMLDialogElement>(null);
  const reasonRef = useRef<HTMLTextAreaElement>(null);
  const [reason, setReason] = useState('');
  useEffect(() => {
    const dialog = ref.current;
    if (dialog === null) return;
    const opener = document.activeElement as HTMLElement | null;
    dialog.showModal();
    reasonRef.current?.focus();
    return () => opener?.focus();
  }, []);
  return (
    <dialog ref={ref} className={styles.dialog} aria-labelledby="confirm-title" onCancel={onClose} onClose={onClose}>
      <h2 id="confirm-title" className={styles.dialogTitle}>
        {title}
      </h2>
      <div className={styles.dialogBody}>{body}</div>
      <label className={styles.dialogLabel} htmlFor="confirm-reason">
        {a.reasonLabel}
      </label>
      <textarea
        ref={reasonRef}
        id="confirm-reason"
        className={styles.textarea}
        maxLength={500}
        aria-describedby="confirm-reason-note"
        value={reason}
        onChange={(e) => setReason(e.target.value)}
      />
      <p className={styles.dialogNote} id="confirm-reason-note">
        {a.reasonNote}
      </p>
      {error === null ? null : (
        <div className={styles.msgErr} role="alert">
          <b>{error}</b>
        </div>
      )}
      <div className={styles.actions}>
        <button
          type="button"
          className={reason.trim() === '' ? styles.btnDisabled : styles.btnPrimary}
          aria-disabled={reason.trim() === ''}
          onClick={() => reason.trim() !== '' && onConfirm(reason.trim())}
        >
          {confirmLabel}
        </button>
        <button type="button" className={styles.btnQuiet} onClick={onClose}>
          {a.keepOpen}
        </button>
      </div>
    </dialog>
  );
}
