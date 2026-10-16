// The room's admin panel (brief 9.3, 9.9, UI-8, FR-63): force-close and cancel, each offered only
// where the API allows it and behind a modal confirmation that names the lot and requires a reason
// written to the audit log. It is in the admin chunk, loaded by the room for an administrator only;
// on success the room's own events (auction_closed) bring the new state.
import { useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import { ApiError } from '../api/client';
import { useApp } from '../app/context';
import type { AuctionStatus } from '../lib/contract';
import { formatINR } from '../lib/money';
import type { RoomState } from '../live/room';
import { ConfirmDialog } from '../room/ConfirmDialog';
import styles from '../room/room.module.css';
import { adminEnIN } from '../strings/admin-en-IN';
import { errorMessage, useStrings } from '../strings/catalogue';
import { roomActions } from './actions';
import { cancelAuction, forceClose } from './api';

type Which = 'force' | 'cancel';

export default function RoomAdminPanel({ state, title, status }: { state: RoomState; title: string; status: AuctionStatus }) {
  const s = useStrings();
  const a = adminEnIN.room;
  const { api } = useApp();
  const queryClient = useQueryClient();
  const [open, setOpen] = useState<Which | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const can = roomActions(status);

  const close = () => {
    setOpen(null);
    setError(null);
  };

  async function run(which: Which, reason: string): Promise<void> {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      if (which === 'force') await forceClose(api, state.auctionId, reason);
      else await cancelAuction(api, state.auctionId, reason);
      await queryClient.invalidateQueries({ queryKey: ['auction', state.auctionId] });
      close();
    } catch (e) {
      // the code's own words: AUCTION_NOT_LIVE, ALREADY_CLOSED, FORBIDDEN, a reason missing
      setError(errorMessage(s, e instanceof ApiError ? e.code : 'SERVICE_UNAVAILABLE'));
    } finally {
      setBusy(false);
    }
  }

  const reserve = state.reserveMet === null ? '' : state.reserveMet ? a.reserveMet : a.reserveNotMet;
  const current = state.price !== null ? a.current(formatINR(state.price), state.sequence, reserve) : a.noBidsYet;
  const reason = { label: a.reasonLabel, note: a.reasonNote };

  return (
    <div className={`${styles.panel} ${styles.proxyPanel}`}>
      <h2>{a.heading}</h2>
      <p className={styles.hint}>{a.note}</p>
      {can.forceClose || can.cancel ? (
        <div className={styles.actions}>
          {can.forceClose ? (
            <button type="button" className={styles.btnQuiet} onClick={() => setOpen('force')}>
              {a.forceClose}
            </button>
          ) : null}
          {can.cancel ? (
            <button type="button" className={styles.btnQuiet} onClick={() => setOpen('cancel')}>
              {a.cancel}
            </button>
          ) : null}
        </div>
      ) : null}
      {open === 'force' ? (
        <ConfirmDialog
          title={a.forceTitle(title)}
          body={
            <>
              <p>{a.forceBody}</p>
              <p>{current}</p>
              <p>{a.cannotUndo}</p>
            </>
          }
          confirmLabel={a.forceClose}
          dismissLabel={a.keepOpen}
          reason={reason}
          error={error}
          onClose={close}
          onConfirm={(r) => void run('force', r)}
        />
      ) : null}
      {open === 'cancel' ? (
        <ConfirmDialog
          title={a.cancelTitle(title)}
          body={<p>{a.cancelBody}</p>}
          confirmLabel={a.cancel}
          dismissLabel={a.keepOpen}
          reason={reason}
          error={error}
          onClose={close}
          onConfirm={(r) => void run('cancel', r)}
        />
      ) : null}
    </div>
  );
}
