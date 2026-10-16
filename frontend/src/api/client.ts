// The REST client (brief 13 items 13, 14, 22, 29). One error envelope becomes ApiError; the bearer
// token comes from memory through deps.getToken; TOKEN_EXPIRED gets one refresh-and-retry;
// placeBid and setProxy retry SERVICE_UNAVAILABLE and network failures with the same request_id
// (never a new one), with a bounded backoff well inside the 10-minute result TTL. Every body
// that carries server_time_ms is offered to the clock as a round-trip sample.
import {
  REQUEST_ID,
  parseAuctionDetail,
  parseAuctionPage,
  parseBidHistoryPage,
  parseCategoryTree,
  parseEmailPreferences,
  parseErrorEnvelope,
  parseFrame,
  parseLiveSnapshot,
  parseNotificationPage,
  parsePaymentConfirm,
  parsePaymentOrder,
  parseSettlement,
  parseTokenResponse,
  parseUserProfile,
  parseWatchlist,
  type AuctionDetail,
  type AuctionPage,
  type BidAccepted,
  type BidHistoryPage,
  type Category,
  type EmailPreferences,
  type LiveSnapshot,
  type NotificationPage,
  type PaymentConfirm,
  type PaymentOrder,
  type ProxyArmed,
  type Settlement,
  type TokenResponse,
  type UserProfile,
  type Watchlist,
} from '../lib/contract';
import type { Sample } from '../lib/clock';

export class ApiError extends Error {
  constructor(
    readonly code: string,
    message: string,
    readonly status: number,
    readonly fields?: Record<string, string>,
    readonly retryAfterMs?: number,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

export type ApiDeps = {
  fetch: typeof fetch;
  now: () => number;
  sleep: (ms: number) => Promise<void>;
  /** the in-memory access token, or null when anonymous */
  getToken: () => string | null;
  /** refresh the access token (auth.ts); null when the session is gone */
  refresh: () => Promise<string | null>;
  /** clock samples from bodies that carry server_time_ms */
  observe?: (sample: Sample) => void;
  /** the in-flight load-time restore (auth.ready): login waits for it, so their cookies never cross */
  ready?: () => Promise<unknown>;
};

export const BASE = '/api/v1';
const BID_BACKOFF_MS = [250, 500, 1000, 2000, 4000]; // between the 6 attempts (item 14)

type Auth = 'none' | 'optional' | 'required';
type Options = { body?: unknown; auth?: Auth; headers?: Record<string, string> };

export type Api = ReturnType<typeof createApi>;

export function createApi(deps: ApiDeps) {
  async function request(method: string, path: string, options: Options = {}): Promise<unknown> {
    const auth = options.auth ?? 'optional';
    let token = auth === 'none' ? null : deps.getToken();
    if (auth === 'required' && token === null) {
      token = await deps.refresh();
      if (token === null) throw new ApiError('INVALID_TOKEN', 'sign in to continue', 401);
    }
    const sentAtMs = deps.now();
    let response = await send(method, path, options, token);
    if (response.status === 401 && token !== null && (await code(response)) === 'TOKEN_EXPIRED') {
      const fresh = await deps.refresh(); // one refresh, one retry
      if (fresh === null) throw new ApiError('TOKEN_EXPIRED', 'sign in again', 401);
      response = await send(method, path, options, fresh);
    }
    const receivedAtMs = deps.now();
    if (response.status === 204) return null;
    const body: unknown = await response
      .clone()
      .json()
      .catch(() => undefined);
    if (!response.ok) throw await toError(response, body);
    if (isStamped(body)) deps.observe?.({ serverTimeMs: body.server_time_ms, sentAtMs, receivedAtMs });
    return body;
  }

  function send(method: string, path: string, options: Options, token: string | null): Promise<Response> {
    const headers: Record<string, string> = { Accept: 'application/json', ...options.headers };
    if (token !== null) headers.Authorization = `Bearer ${token}`;
    const init: RequestInit = { method, headers, credentials: 'same-origin' };
    if (options.body instanceof Blob) {
      // a file (an image upload) is the body itself; a Blob can be sent again on the retry
      headers['Content-Type'] = options.body.type || 'application/octet-stream';
      init.body = options.body;
    } else if (options.body !== undefined) {
      headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(options.body);
    }
    return deps.fetch(BASE + path, init);
  }

  /** A bid or a maximum: the same request_id on every attempt (item 14). */
  async function withRetries<T>(attempt: () => Promise<T>): Promise<T> {
    for (let i = 0; ; i++) {
      try {
        return await attempt();
      } catch (error) {
        const retryable =
          (error instanceof ApiError && error.code === 'SERVICE_UNAVAILABLE') ||
          (error instanceof TypeError && !(error instanceof ApiError)); // fetch: network failure
        const wait = BID_BACKOFF_MS[i];
        if (!retryable || wait === undefined) throw error;
        await deps.sleep(wait);
      }
    }
  }

  function checkRequestId(requestId: string): void {
    if (!REQUEST_ID.test(requestId)) throw new ApiError('VALIDATION_ERROR', 'bad request_id', 422);
  }

  return {
    request,

    /** anonymous: no token even when one is held (a revoked session polls the public view) */
    async live(auctionId: string, anonymous = false): Promise<LiveSnapshot> {
      return parsed(await request('GET', `/auctions/${auctionId}/live`, anonymous ? { auth: 'none' } : {}), parseLiveSnapshot);
    },

    async detail(auctionId: string): Promise<AuctionDetail> {
      return parsed(await request('GET', `/auctions/${auctionId}`), parseAuctionDetail);
    },

    async history(auctionId: string, before?: number, limit = 20): Promise<BidHistoryPage> {
      const query = new URLSearchParams({ limit: String(limit) });
      if (before !== undefined) query.set('before', String(before));
      return parsed(await request('GET', `/auctions/${auctionId}/bids?${query}`), parseBidHistoryPage);
    },

    // discovery (FR-20 to FR-23): the query string is built by discover/params.ts
    async auctions(query: URLSearchParams): Promise<AuctionPage> {
      return parsed(await request('GET', `/auctions?${query}`), parseAuctionPage);
    },
    async categories(): Promise<Category[]> {
      return parsed(await request('GET', '/categories', { auth: 'none' }), parseCategoryTree);
    },

    // the watchlist (FR-24, FR-25): adding twice is a no-op on the server
    async watchlist(): Promise<Watchlist> {
      return parsed(await request('GET', '/watchlist', { auth: 'required' }), parseWatchlist);
    },
    async watch(auctionId: string): Promise<void> {
      await request('PUT', `/watchlist/${auctionId}`, { auth: 'required' });
    },
    async unwatch(auctionId: string): Promise<void> {
      await request('DELETE', `/watchlist/${auctionId}`, { auth: 'required' });
    },

    // notifications (FR-74): newest first, keyset on id
    async notifications(options: { unread?: boolean; before?: number; limit?: number } = {}): Promise<NotificationPage> {
      const query = new URLSearchParams();
      if (options.unread) query.set('unread', 'true');
      query.set('limit', String(options.limit ?? 20));
      if (options.before !== undefined) query.set('before', String(options.before));
      return parsed(await request('GET', `/notifications?${query}`, { auth: 'required' }), parseNotificationPage);
    },
    async markRead(id: number): Promise<void> {
      await request('POST', `/notifications/${id}/read`, { auth: 'required' });
    },
    async markAllRead(): Promise<number> {
      const body = await request('POST', '/notifications/read-all', { auth: 'required' });
      const updated = (body as { updated?: unknown } | null)?.updated;
      if (typeof updated !== 'number') throw malformed();
      return updated;
    },
    async emailPreferences(): Promise<EmailPreferences> {
      return parsed(await request('GET', '/notifications/preferences', { auth: 'required' }), parseEmailPreferences);
    },
    async setEmailPreferences(email: EmailPreferences): Promise<EmailPreferences> {
      return parsed(await request('PUT', '/notifications/preferences', { auth: 'required', body: { email } }), parseEmailPreferences);
    },

    // payments (FR-65 to FR-69, DC-10): the client never sends an amount; a start has no body at
    // all, a confirmation only the three ids the gateway's checkout handed back
    async settlement(auctionId: string): Promise<Settlement> {
      return parsed(await request('GET', `/auctions/${auctionId}/settlement`, { auth: 'required' }), parseSettlement);
    },
    async startPayment(auctionId: string): Promise<PaymentOrder> {
      return parsed(await request('POST', `/auctions/${auctionId}/payment`, { auth: 'required' }), parsePaymentOrder);
    },
    async confirmPayment(
      auctionId: string,
      ids: { razorpay_order_id: string; razorpay_payment_id: string; razorpay_signature: string },
    ): Promise<PaymentConfirm> {
      const body = { razorpay_order_id: ids.razorpay_order_id, razorpay_payment_id: ids.razorpay_payment_id, razorpay_signature: ids.razorpay_signature };
      return parsed(await request('POST', `/auctions/${auctionId}/payment/confirm`, { auth: 'required', body }), parsePaymentConfirm);
    },

    async me(): Promise<UserProfile> {
      return parsed(await request('GET', '/users/me', { auth: 'required' }), parseUserProfile);
    },

    // identity (brief 9.10); the refresh cookie is set by the login response itself
    async register(email: string, password: string, fullName: string): Promise<UserProfile> {
      const body = { email, password, full_name: fullName };
      return parsed(await request('POST', '/auth/register', { auth: 'none', body }), parseUserProfile);
    },
    async verifyEmail(token: string): Promise<void> {
      await request('POST', '/auth/verify-email', { auth: 'none', body: { token } });
    },
    async resendVerification(email: string): Promise<void> {
      await request('POST', '/auth/resend-verification', { auth: 'none', body: { email } });
    },
    async login(email: string, password: string): Promise<TokenResponse> {
      await deps.ready?.(); // the page's restore answers first (no login cookie yet), never after
      const body = { email, password };
      return parsed(await request('POST', '/auth/login', { auth: 'none', body }), parseTokenResponse);
    },
    async forgotPassword(email: string): Promise<void> {
      await request('POST', '/auth/forgot-password', { auth: 'none', body: { email } });
    },
    async resetPassword(token: string, password: string): Promise<void> {
      await request('POST', '/auth/reset-password', { auth: 'none', body: { token, password } });
    },

    async placeBid(auctionId: string, amount: number, requestId: string): Promise<BidAccepted> {
      checkRequestId(requestId);
      return withRetries(async () => {
        const body = await request('POST', `/auctions/${auctionId}/bids`, {
          auth: 'required',
          body: { amount, request_id: requestId },
        });
        const frame = parseFrame(body);
        if (frame?.type !== 'bid_accepted') throw malformed();
        return frame;
      });
    },

    async setProxy(auctionId: string, maxAmount: number, requestId: string): Promise<ProxyArmed> {
      checkRequestId(requestId);
      return withRetries(async () => {
        const body = await request('POST', `/auctions/${auctionId}/proxy`, {
          auth: 'required',
          body: { max_amount: maxAmount, request_id: requestId },
        });
        const frame = parseFrame(body);
        if (frame?.type !== 'proxy_armed') throw malformed();
        return frame;
      });
    },
  };
}

export function parsed<T>(body: unknown, parse: (v: unknown) => T | null): T {
  const value = parse(body);
  if (value === null) throw malformed();
  return value;
}

function malformed(): ApiError {
  return new ApiError('INTERNAL_ERROR', 'the server sent something this client does not understand', 0);
}

function isStamped(body: unknown): body is { server_time_ms: number } {
  return (
    typeof body === 'object' &&
    body !== null &&
    Number.isSafeInteger((body as { server_time_ms?: unknown }).server_time_ms)
  );
}

async function code(response: Response): Promise<string | null> {
  const body: unknown = await response
    .clone()
    .json()
    .catch(() => undefined);
  return parseErrorEnvelope(body)?.error.code ?? null;
}

async function toError(response: Response, body: unknown): Promise<ApiError> {
  const envelope = parseErrorEnvelope(body);
  const retryAfter = response.headers.get('Retry-After');
  const retryAfterMs = retryAfter !== null && /^\d+$/.test(retryAfter) ? Number(retryAfter) * 1000 : undefined;
  if (envelope === null) {
    return new ApiError(`HTTP_${response.status}`, response.statusText || 'request failed', response.status);
  }
  return new ApiError(
    envelope.error.code,
    envelope.error.message,
    response.status,
    envelope.error.fields,
    retryAfterMs,
  );
}
