// A closed listing's result for its seller (brief 9.8; FR-64, FR-69, BR-10): sold is the payment
// page's own two lines (who won at what price, then the payment status), not sold says whether the
// reserve was the reason. The reserve amount is not in it: the page around it is the seller's own.
import type { AuctionStatus, Settlement } from '../lib/contract';
import { sellerLines } from '../pay/seller';
import { payEnIN } from '../strings/pay-en-IN';
import { sellerEnIN as t } from '../strings/seller-en-IN';

export function resultLines(status: AuctionStatus, reserveMet: boolean | null, settlement: Settlement | undefined, at: (ms: number) => string): string[] {
  if (status === 'closed_no_sale') return [reserveMet === false ? `${t.closed.notSold} ${t.closed.reserveNotMet}` : t.closed.notSold];
  if (status === 'closed_sold' && settlement !== undefined) return sellerLines(settlement, payEnIN, at);
  return [];
}
