// Razorpay Checkout (DC-10, SI-3, brief 13 item 25): card and UPI entry happen only inside the
// gateway's own window; this page loads checkout.js once, on the viewer's first "Pay", opens it
// for the server's order (its key, order id and amount, never one chosen here) and checks the ids
// the window hands back against the server's patterns (schemas/payments.py) before posting them.
import type { PaymentOrder } from '../lib/contract';

export const CHECKOUT_SRC = 'https://checkout.razorpay.com/v1/checkout.js';

export type CheckoutIds = { razorpay_order_id: string; razorpay_payment_id: string; razorpay_signature: string };

export type RazorpayOptions = {
  key: string;
  order_id: string;
  amount: number;
  currency: string;
  name: string;
  description: string;
  theme: { color: string };
  handler: (response: unknown) => void;
  modal: { ondismiss: () => void; escape: boolean };
};
export type RazorpayInstance = { open: () => void; on: (event: 'payment.failed', listener: (response: unknown) => void) => void };
export type RazorpayCtor = new (options: RazorpayOptions) => RazorpayInstance;

type Doc = { createElement: (tag: 'script') => HTMLScriptElement; head: { appendChild: (el: HTMLScriptElement) => unknown } };
type Win = { Razorpay?: RazorpayCtor };

/** One shared load per page; a failed load is forgotten, so the next "Pay" tries again. */
export function createLoader(doc: Doc, win: Win): () => Promise<RazorpayCtor> {
  let loading: Promise<RazorpayCtor> | null = null;
  return () => {
    loading ??= new Promise<RazorpayCtor>((resolve, reject) => {
      if (win.Razorpay !== undefined) {
        resolve(win.Razorpay);
        return;
      }
      const script = doc.createElement('script');
      script.src = CHECKOUT_SRC;
      script.async = true;
      script.onload = () => (win.Razorpay === undefined ? reject(new Error('checkout.js loaded without Razorpay')) : resolve(win.Razorpay));
      script.onerror = () => reject(new Error('checkout.js did not load'));
      doc.head.appendChild(script);
    });
    loading.catch(() => {
      loading = null;
    });
    return loading;
  };
}

const ORDER = /^order_[A-Za-z0-9]{1,34}$/;
const PAYMENT = /^pay_[A-Za-z0-9]{1,36}$/;
const SIGNATURE = /^[0-9a-f]{64}$/;

/** The three ids from the checkout's success handler, or null when any does not look right. */
export function checkoutIds(response: unknown): CheckoutIds | null {
  if (typeof response !== 'object' || response === null) return null;
  const r = response as Record<string, unknown>;
  const { razorpay_order_id: order, razorpay_payment_id: payment, razorpay_signature: signature } = r;
  if (typeof order !== 'string' || typeof payment !== 'string' || typeof signature !== 'string') return null;
  if (!ORDER.test(order) || !PAYMENT.test(payment) || !SIGNATURE.test(signature)) return null;
  return { razorpay_order_id: order, razorpay_payment_id: payment, razorpay_signature: signature };
}

/** The checkout's options for the server's order. No prefill: nothing personal goes to the gateway. */
export function checkoutOptions(
  order: PaymentOrder,
  page: { name: string; description: string; color: string; handler: (response: unknown) => void; ondismiss: () => void },
): RazorpayOptions {
  return {
    key: order.key_id,
    order_id: order.order_id,
    amount: order.amount,
    currency: order.currency,
    name: page.name,
    description: page.description,
    theme: { color: page.color },
    handler: page.handler,
    modal: { ondismiss: page.ondismiss, escape: true },
  };
}
