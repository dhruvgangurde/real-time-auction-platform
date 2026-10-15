// The seller's view of the payment page (FR-64, FR-69): who won and at what price, the winner's
// name being the seller's to see once a settlement exists (BR-10), and whether the payment has
// arrived. The buyer's attempts are not shown: a failed try is not the seller's business.
import type { Settlement } from '../lib/contract';
import { formatINR } from '../lib/money';
import type { PayCatalogue } from '../strings/catalogue';

export function sellerLines(s: Settlement, p: PayCatalogue, at: (ms: number) => string): string[] {
  const won = p.statusWon(s.winner?.full_name ?? '', s.final_price === null ? '' : formatINR(s.final_price));
  const state = s.status === 'paid' && s.paid_at_ms !== null ? p.paidOn(at(s.paid_at_ms)) : p.statusAwaiting;
  return [won, state].filter((line) => line !== '');
}
