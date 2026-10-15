// One row per notification type (brief 9.6; FR-70, FR-68, 4.10.2 S3), built from the payload the
// producer stored (services/notifications.py, closing.py, payments.py). A missing field or an
// unknown type falls back to a plain line; no row ever carries a reserve amount or a maximum.
import { describe, expect, test } from 'vitest';

import type { NotificationItem, NotificationPage } from '../lib/contract';
import { enIN } from '../strings/en-IN';
import { markReadIn, rowText, TYPES } from './rows';

const A = 'b6595bf0-9c27-4003-8a56-e441af8ddb82';
const n = (type: string, payload: Record<string, unknown>, over: Partial<NotificationItem> = {}): NotificationItem => ({
  id: 1, type, auction_id: A, payload: { auction_id: A, title: 'Pentax K1000', ...payload }, read: false, created_at_ms: 0, ...over,
});

describe('a sentence for each of the nine types', () => {
  test('every type the backend stores has a label and a sentence', () => {
    expect(TYPES).toEqual(['outbid', 'ending_soon', 'auction_won', 'auction_lost', 'payment_due', 'payment_received', 'payment_failed', 'auction_sold', 'auction_no_sale']);
    const samples: Record<string, Record<string, unknown>> = {
      outbid: { current_price: 950000, sequence_no: 4 },
      ending_soon: { end_time_ms: Date.UTC(2026, 9, 14, 14, 30) },
      auction_won: { outcome: 'closed_sold', final_price: 1200000, reserve_met: true },
      auction_lost: { outcome: 'closed_sold', final_price: 1200000, reserve_met: true },
      payment_due: { outcome: 'closed_sold', final_price: 1200000, reserve_met: true },
      payment_received: { amount: 1200000, settlement_id: 's' },
      payment_failed: { amount: 1200000, error_code: 'BAD_REQUEST_ERROR' },
      auction_sold: { outcome: 'closed_sold', final_price: 1200000, reserve_met: true },
      auction_no_sale: { outcome: 'closed_no_sale', final_price: null, reserve_met: false },
    };
    const texts = TYPES.map((t) => rowText(n(t, samples[t]!), enIN));
    for (const [i, row] of texts.entries()) {
      expect(row.label, TYPES[i]).not.toBe('');
      expect(row.text, TYPES[i]).toContain('Pentax K1000');
      // the payment types open the payment page, its own document (a full load); the rest the room
      const pay = ['payment_due', 'payment_received', 'payment_failed'].includes(TYPES[i]!);
      expect(row.href, TYPES[i]).toBe(pay ? `/auctions/${A}/pay` : `/auctions/${A}`);
      expect(row.document, TYPES[i]).toBe(pay);
    }
    expect(texts[0]!.text).toBe('You were outbid on Pentax K1000. The current bid is ₹9,500.');
    expect(texts[2]!.text).toBe('Pentax K1000 is yours at ₹12,000.');
    expect(texts[4]!.text).toBe('₹12,000 for Pentax K1000.');
    expect(texts[6]!.text).not.toContain('BAD_REQUEST_ERROR'); // the gateway's code is not for people
    expect(texts[8]!.text).toBe('Pentax K1000 closed without a sale. The reserve was not met.');
  });

  test('a missing field or a wrong type falls back to a plain line with the title', () => {
    expect(rowText(n('outbid', { current_price: 'lots' }), enIN).text).toBe('There is an update on Pentax K1000.');
    expect(rowText(n('payment_due', { final_price: null }), enIN).text).toBe('There is an update on Pentax K1000.');
    expect(rowText(n('auction_cancelled', {}), enIN)).toMatchObject({ label: enIN.notificationsPage.labels.other, text: 'There is an update on Pentax K1000.' });
  });

  test('without a title or an auction there is still a line, and no link', () => {
    expect(rowText(n('outbid', { title: 7 }, { auction_id: null }), enIN)).toEqual({
      label: enIN.notificationsPage.labels.outbid,
      text: 'There is an update on one of your auctions.',
      href: null,
      document: false,
    });
  });

  test('no row shows a reserve amount, whatever the payload carries', () => {
    const row = rowText(n('auction_no_sale', { reserve_met: false, reserve_price: 1025000 }), enIN);
    expect(row.text).not.toMatch(/10,250|1025000/);
  });
});

describe('marking read in the cached pages', () => {
  const page = (ids: number[], read = false): NotificationPage => ({ items: ids.map((id) => ({ ...n('outbid', {}), id, read })), next_before: null });
  const data = { pages: [page([5, 4]), page([3])], pageParams: [undefined, 4] };

  test('one id: that row turns read, nothing else changes', () => {
    const next = markReadIn(data, 4);
    expect(next.pages.flatMap((p) => p.items.map((i) => [i.id, i.read]))).toEqual([[5, false], [4, true], [3, false]]);
    expect(next.pageParams).toBe(data.pageParams);
  });

  test('all: every row turns read', () => {
    expect(markReadIn(data, 'all').pages.flatMap((p) => p.items.map((i) => i.read))).toEqual([true, true, true]);
  });
});
