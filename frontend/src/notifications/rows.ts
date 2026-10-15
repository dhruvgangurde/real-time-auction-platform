// A notification row's label and sentence (brief 9.6), from the payload its producer stored:
// outbid {title, current_price}; ending_soon {title, end_time_ms}; the close types {title,
// outcome, final_price, reserve_met}; payment_received {title, amount}; payment_failed {title,
// amount, error_code}. Fields are read defensively: a missing or mistyped one gives the plain
// line. Only declared fields are read, so a reserve amount or a maximum is never shown (BR-6,
// BR-7); the gateway's error code is not shown either.
import type { InfiniteData } from '@tanstack/react-query';

import { formatDay, formatLocal } from '../lib/clock';
import type { NotificationItem, NotificationPage } from '../lib/contract';
import { formatINR, isPaise } from '../lib/money';
import type { Catalogue } from '../strings/catalogue';

/** The types the backend stores (schemas/notifications.py), in the order of the preferences. */
export const TYPES = [
  'outbid',
  'ending_soon',
  'auction_won',
  'auction_lost',
  'payment_due',
  'payment_received',
  'payment_failed',
  'auction_sold',
  'auction_no_sale',
] as const;
export type NotificationType = (typeof TYPES)[number];

const isType = (t: string): t is NotificationType => (TYPES as readonly string[]).includes(t);

/** `document`: the link opens another document (the payment page), so it is a full load. */
export type Row = { label: string; text: string; href: string | null; document: boolean };

const PAYMENT_TYPES = new Set(['payment_due', 'payment_received', 'payment_failed']);

export function rowText(n: NotificationItem, s: Catalogue): Row {
  const p = s.notificationsPage;
  const title = typeof n.payload.title === 'string' ? n.payload.title : '';
  const money = (v: unknown) => (isPaise(v) ? formatINR(v) : '');
  const at = (v: unknown) => (typeof v === 'number' && Number.isSafeInteger(v) ? `${formatDay(v)}, ${formatLocal(v)}` : '');
  const t = p.text;
  let text = '';
  if (isType(n.type)) {
    const pl = n.payload;
    const sold = pl.outcome === 'closed_sold';
    const byType: Record<NotificationType, () => string> = {
      outbid: () => t.outbid(title, money(pl.current_price)),
      ending_soon: () => t.endingSoon(title, at(pl.end_time_ms)),
      auction_won: () => t.won(title, money(pl.final_price)),
      auction_lost: () => (sold ? t.lostSold(title, money(pl.final_price)) : t.lostNoSale(title)),
      payment_due: () => t.paymentDue(money(pl.final_price), title),
      payment_received: () => t.paymentReceived(money(pl.amount), title),
      payment_failed: () => t.paymentFailed(money(pl.amount), title),
      auction_sold: () => t.sold(title, money(pl.final_price)),
      auction_no_sale: () => (pl.reserve_met === false ? t.noSaleReserve(title) : t.noSale(title)),
    };
    text = byType[n.type]();
  }
  return {
    label: isType(n.type) ? p.labels[n.type] : p.labels.other,
    text: text || t.other(title) || t.otherNoTitle,
    // the payment types open the payment page (its own document); the rest the auction room
    href: n.auction_id === null ? null : PAYMENT_TYPES.has(n.type) ? `/auctions/${n.auction_id}/pay` : `/auctions/${n.auction_id}`,
    document: n.auction_id !== null && PAYMENT_TYPES.has(n.type),
  };
}

/** The cached list with one row (or every row) read: the optimistic step of mark-read. */
export function markReadIn(data: InfiniteData<NotificationPage, number | undefined>, id: number | 'all'): InfiniteData<NotificationPage, number | undefined> {
  return {
    ...data,
    pages: data.pages.map((page) => ({ ...page, items: page.items.map((i) => (id === 'all' || i.id === id ? { ...i, read: true } : i)) })),
  };
}
