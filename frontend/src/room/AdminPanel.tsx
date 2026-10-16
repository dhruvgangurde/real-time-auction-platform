// The admin panel (brief 9.3, 9.9, UI-8, FR-63): force-close and cancel, each behind a modal
// confirmation that requires a reason written to the audit log. The actions call the admin API;
// on success the room's own events (auction_closed) bring the new state.
import { useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import { useApp } from '../app/context';
import { formatINR } from '../lib/money';
import type { RoomState } from '../live/room';
import { useStrings } from '../strings/catalogue';
import { ConfirmDialog } from './ConfirmDialog';
import styles from './room.module.css';

type Which = 'force' | 'cancel';

export function AdminPanel({ state }: { state: RoomState }) {
  const a = useStrings().roomUi.admin;
  const { api } = useApp();
  const queryClient = useQueryClient();
  const [open, setOpen] = useState<Which | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const close = () => {
    setOpen(null);
    setError(null);
  };

  async function run(which: Which, reason: string): Promise<void> {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      if (which === 'force') await api.adminForceClose(state.auctionId, reason);
      else await api.adminCancel(state.auctionId, reason);
      await queryClient.invalidateQueries({ queryKey: ['auction', state.auctionId] });
      close();
    } catch {
      setError(a.failed);
    } finally {
      setBusy(false);
    }
  }

  const reserve = state.reserveMet === null ? '' : state.reserveMet ? a.reserveMet : a.reserveNotMet;
  const current =
    state.price !== null ? a.current(formatINR(state.price), state.sequence, reserve) : a.noBidsYet;
  const forceBody = (
    <>
      <p>{a.forceBody}</p>
      <p>{current}</p>
      <p>{a.cannotUndo}</p>
    </>
  );
  const cancelBody = (
    <>
      <p>{a.cancelBody}</p>
    </>
  );

  return (
    <div className={`${styles.panel} ${styles.proxyPanel}`}>
      <h2>{a.heading}</h2>
      <p className={styles.hint}>{a.note}</p>
      <div className={styles.actions}>
        <button type="button" className={styles.btnQuiet} onClick={() => setOpen('force')} disabled={state.terminal !== null}>
          {a.forceClose}
        </button>
        <button type="button" className={styles.btnQuiet} onClick={() => setOpen('cancel')} disabled={state.terminal !== null}>
          {a.cancel}
        </button>
      </div>
      {open === 'force' ? (
        <ConfirmDialog title={a.forceTitle} body={forceBody} confirmLabel={a.forceClose} dismissLabel={a.keepOpen} reason={{ label: a.reasonLabel, note: a.reasonNote }} error={error} onClose={close} onConfirm={(r) => void run('force', r)} />
      ) : null}
      {open === 'cancel' ? (
        <ConfirmDialog title={a.cancelTitle} body={cancelBody} confirmLabel={a.cancel} dismissLabel={a.keepOpen} reason={{ label: a.reasonLabel, note: a.reasonNote }} error={error} onClose={close} onConfirm={(r) => void run('cancel', r)} />
      ) : null}
    </div>
  );
}
