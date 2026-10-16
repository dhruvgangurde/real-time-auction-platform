// A native modal <dialog> for a confirmation (UI-8): with `reason`, a required reason and the
// confirm disabled until it is typed (administrative actions); Escape and a quiet dismiss button;
// focus returned to the control that opened it. The caller runs the action (with the reason).
import { useEffect, useRef, useState } from 'react';

import styles from './room.module.css';

export function ConfirmDialog({
  title,
  body,
  confirmLabel,
  dismissLabel,
  reason: asked,
  onConfirm,
  onClose,
  error,
}: {
  title: string;
  body: React.ReactNode;
  confirmLabel: string;
  dismissLabel: string;
  reason?: { label: string; note: string };
  onConfirm: (reason: string) => void;
  onClose: () => void;
  error: string | null;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const reasonRef = useRef<HTMLTextAreaElement>(null);
  const dismissRef = useRef<HTMLButtonElement>(null);
  const [reason, setReason] = useState('');
  const missing = asked !== undefined && reason.trim() === '';
  useEffect(() => {
    const dialog = ref.current;
    if (dialog === null) return;
    const opener = document.activeElement as HTMLElement | null;
    dialog.showModal();
    // the reason to type, or else the safe choice
    (reasonRef.current ?? dismissRef.current)?.focus();
    return () => opener?.focus();
  }, []);
  return (
    <dialog ref={ref} className={styles.dialog} aria-labelledby="confirm-title" onCancel={onClose} onClose={onClose}>
      <h2 id="confirm-title" className={styles.dialogTitle}>
        {title}
      </h2>
      <div className={styles.dialogBody}>{body}</div>
      {asked === undefined ? null : (
        <>
          <label className={styles.dialogLabel} htmlFor="confirm-reason">
            {asked.label}
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
            {asked.note}
          </p>
        </>
      )}
      {error === null ? null : (
        <div className={styles.msgErr} role="alert">
          <b>{error}</b>
        </div>
      )}
      <div className={styles.actions}>
        <button
          type="button"
          className={missing ? styles.btnDisabled : styles.btnPrimary}
          aria-disabled={missing}
          onClick={() => !missing && onConfirm(reason.trim())}
        >
          {confirmLabel}
        </button>
        <button ref={dismissRef} type="button" className={styles.btnQuiet} onClick={onClose}>
          {dismissLabel}
        </button>
      </div>
    </dialog>
  );
}
