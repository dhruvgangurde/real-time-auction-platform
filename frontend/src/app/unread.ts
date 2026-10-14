// The header's unread count (brief 9.1, 9.6; FR-74). There is no count endpoint (G5): the count
// is one page of unread notifications of up to 100, shown up to 99 and then as "99+". It is
// polled every 30 s while the tab is visible and refetched when the tab is shown again; a room's
// live `notification` frame refetches it at once (G6: only a socket hears those frames).
import type { NotificationPage } from '../lib/contract';

export const UNREAD_LIMIT = 100;
export const UNREAD_POLL_MS = 30_000;

export function unreadCount(page: NotificationPage): number {
  return page.items.filter((n) => !n.read).length;
}

/** '' for none, "1" to "99", then "99+". */
export function badge(count: number): string {
  return count === 0 ? '' : count > 99 ? '99+' : String(count);
}

/** A rise after the first read is a new arrival, announced politely; the first read is not. */
export function arrived(previous: number | undefined, next: number): boolean {
  return previous !== undefined && next > previous;
}
