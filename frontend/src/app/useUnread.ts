// The unread count's query, shared by the bell (which also announces a rise) and the
// notifications page (which only reads it): one cache entry per user (unread.ts).
import { useQuery } from '@tanstack/react-query';

import { useApp, useToken, useViewerId } from './context';
import { unreadCount, UNREAD_LIMIT, UNREAD_POLL_MS } from './unread';

export function useUnreadCount(): { count: number; loaded: boolean; viewer: string | null } {
  const { api } = useApp();
  const token = useToken();
  const viewer = useViewerId();
  const unread = useQuery({
    queryKey: ['notifications', 'unread', viewer],
    queryFn: () => api.notifications({ unread: true, limit: UNREAD_LIMIT }),
    enabled: token !== null,
    refetchInterval: UNREAD_POLL_MS, // paused while the tab is hidden
    refetchOnWindowFocus: true, // and read again when it is shown
  });
  const loaded = token !== null && unread.data !== undefined;
  return { count: loaded && unread.data !== undefined ? unreadCount(unread.data) : 0, loaded, viewer };
}
