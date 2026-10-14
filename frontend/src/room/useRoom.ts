// The React side of a room session: one session per auction, created once the load-time
// restore settled (so a signed-in viewer claims the socket), started and stopped with the
// component, read through useSyncExternalStore.
import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useSyncExternalStore } from 'react';

import { useApp, useAuthReady } from '../app/context';
import { initialRoom, type RoomState } from '../live/room';
import { announcePolite } from '../lib/polite';
import { useStrings } from '../strings/catalogue';
import { createRoomSession, type RoomSession } from './session';

const none = () => () => undefined;

export function useRoom(auctionId: string, enabled = true): { state: RoomState; session: RoomSession | null } {
  const { api, auth, clock } = useApp();
  const catalogue = useStrings();
  const ready = useAuthReady();
  const client = useQueryClient();
  const session = useMemo(
    () =>
      ready && enabled
        ? createRoomSession({
            auctionId,
            api,
            auth,
            clock,
            catalogue,
            polite: announcePolite,
            onNotification: () => void client.invalidateQueries({ queryKey: ['notifications'] }),
            assertiveRegion: () => document.getElementById('live-assertive'),
          })
        : null,
    [auctionId, api, auth, clock, catalogue, ready, enabled, client],
  );
  useEffect(() => {
    if (session === null) return;
    session.start();
    return () => session.stop();
  }, [session]);
  const empty = useMemo(() => initialRoom(auctionId, 'polling'), [auctionId]);
  const state = useSyncExternalStore(
    session?.subscribe ?? none,
    () => session?.getState() ?? empty,
    () => empty,
  );
  return { state, session };
}
