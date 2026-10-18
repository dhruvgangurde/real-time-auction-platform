// The payment page as a state machine (brief 9.5; FR-65 to FR-69). Who may pay comes from the
// settlement: its winner pays, the seller (or an admin) reads the status, a no-sale has nothing to
// pay, and a demo lot's sale has nothing to pay for anyone (no Pay at all). Razorpay's checkout
// handing back ids means only "confirming": the page says Paid when the
// settlement read says paid (the checkout callback, the webhook or the reconciler completed it),
// never before. A failure is the server's answer or a failure inside the open window. An amount
// below the gateway's minimum charge can never be paid online, so that refusal ends the paying.
import type { Settlement } from '../lib/contract';

export type PayState =
  | { kind: 'loading' }
  | { kind: 'signedOut' }
  | { kind: 'unreachable' }
  | { kind: 'error'; code: string }
  | { kind: 'refused'; code: string }
  | { kind: 'noSale' }
  /** a demo lot's sale: nothing to pay, for anyone (B2 Q1; the server refuses DEMO_NOT_PAYABLE) */
  | { kind: 'demo' }
  | { kind: 'status'; settlement: Settlement }
  | { kind: 'due'; amount: number; lastFailed: boolean; notice: string | null; retryAfterMs: number | null }
  | { kind: 'opening'; amount: number }
  | { kind: 'open'; amount: number; failedInWindow: boolean }
  | { kind: 'confirming'; amount: number; unverified: boolean }
  | { kind: 'failed'; amount: number }
  | { kind: 'belowMinimum'; amount: number }
  | { kind: 'paid'; amount: number; paidAtMs: number | null; paymentId: string | null };

export type PayEvent =
  | { type: 'session'; state: 'signedOut' | 'unreachable' | 'loading' }
  | { type: 'loaded'; settlement: Settlement; viewerId: string }
  | { type: 'loadFailed'; code: string }
  | { type: 'pay' }
  | { type: 'opened' }
  | { type: 'startFailed'; code: string; retryAfterMs?: number }
  | { type: 'scriptFailed' }
  | { type: 'gatewayFailed' }
  | { type: 'dismissed' }
  | { type: 'authorized' }
  | { type: 'confirmed'; status: 'paid' | 'failed' | 'pending' }
  | { type: 'confirmFailed'; code: string }
  | { type: 'polled'; settlement: Settlement };

const SIGNED_OUT = new Set(['INVALID_TOKEN', 'TOKEN_EXPIRED']);
const REFUSED = new Set(['FORBIDDEN', 'NOT_FOUND', 'NOT_WINNER']);
// the server could not tie these ids to the stored order: say so, keep reading the settlement
const UNVERIFIED = new Set(['INVALID_SIGNATURE', 'PAYMENT_MISMATCH']);

const due = (amount: number, notice: string | null = null, retryAfterMs: number | null = null, lastFailed = false): PayState => ({
  kind: 'due',
  amount,
  lastFailed,
  notice,
  retryAfterMs,
});

function paid(s: Settlement): PayState {
  return { kind: 'paid', amount: s.final_price ?? 0, paidAtMs: s.paid_at_ms, paymentId: s.payment?.payment_id ?? null };
}

function fromSettlement(s: Settlement, viewerId: string): PayState {
  if (s.status === 'no_sale') return { kind: 'noSale' };
  if (s.status === 'demo') return { kind: 'demo' };
  if (s.winner?.id !== viewerId) return { kind: 'status', settlement: s }; // the seller or an admin (FR-69)
  if (s.status === 'paid') return paid(s);
  return due(s.final_price ?? 0, null, null, s.payment?.status === 'failed');
}

export function payReducer(s: PayState, e: PayEvent): PayState {
  switch (e.type) {
    case 'session':
      return { kind: e.state };
    case 'loaded':
      return fromSettlement(e.settlement, e.viewerId);
    case 'loadFailed':
      if (SIGNED_OUT.has(e.code)) return { kind: 'signedOut' };
      return REFUSED.has(e.code) ? { kind: 'refused', code: e.code } : { kind: 'error', code: e.code };
    case 'pay':
      return s.kind === 'due' || s.kind === 'failed' ? { kind: 'opening', amount: s.amount } : s;
    case 'opened':
      return s.kind === 'opening' ? { kind: 'open', amount: s.amount, failedInWindow: false } : s;
    case 'startFailed':
      if (s.kind !== 'opening') return s;
      if (e.code === 'ALREADY_PAID') return { kind: 'loading' }; // read it again: the receipt
      if (e.code === 'AMOUNT_BELOW_GATEWAY_MINIMUM') return { kind: 'belowMinimum', amount: s.amount }; // no retry changes the amount
      if (REFUSED.has(e.code)) return { kind: 'refused', code: e.code };
      if (SIGNED_OUT.has(e.code)) return { kind: 'signedOut' };
      return due(s.amount, e.code, e.retryAfterMs ?? null);
    case 'scriptFailed':
      return s.kind === 'opening' ? due(s.amount, 'SCRIPT') : s;
    case 'gatewayFailed':
      return s.kind === 'open' ? { ...s, failedInWindow: true } : s;
    case 'dismissed':
      if (s.kind !== 'open') return s;
      return s.failedInWindow ? { kind: 'failed', amount: s.amount } : due(s.amount, 'DISMISSED');
    case 'authorized':
      return s.kind === 'open' ? { kind: 'confirming', amount: s.amount, unverified: false } : s;
    case 'confirmed':
      // paid and pending both wait for the settlement read; failed is the server's answer
      return s.kind === 'confirming' && e.status === 'failed' ? { kind: 'failed', amount: s.amount } : s;
    case 'confirmFailed':
      return s.kind === 'confirming' && UNVERIFIED.has(e.code) ? { ...s, unverified: true } : s;
    case 'polled':
      return s.kind === 'confirming' && e.settlement.status === 'paid' ? paid(e.settlement) : s;
  }
}
