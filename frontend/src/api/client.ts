// The REST client (brief 13 items 13, 14, 22, 29). One error envelope becomes ApiError; the bearer
// token comes from memory through deps.getToken; TOKEN_EXPIRED gets one refresh-and-retry;
// placeBid and setProxy retry SERVICE_UNAVAILABLE and network failures with the same request_id
// (never a new one), with a bounded backoff well inside the 10-minute result TTL. Every body
// that carries server_time_ms is offered to the clock as a round-trip sample.
import {
  REQUEST_ID,
  parseAuctionDetail,
  parseBidHistoryPage,
  parseErrorEnvelope,
  parseFrame,
  parseLiveSnapshot,
  parseUserProfile,
  type AuctionDetail,
  type BidAccepted,
  type BidHistoryPage,
  type LiveSnapshot,
  type ProxyArmed,
  type UserProfile,
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
    if (options.body !== undefined) headers['Content-Type'] = 'application/json';
    const init: RequestInit = { method, headers, credentials: 'same-origin' };
    if (options.body !== undefined) init.body = JSON.stringify(options.body);
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

    async live(auctionId: string): Promise<LiveSnapshot> {
      return parsed(await request('GET', `/auctions/${auctionId}/live`), parseLiveSnapshot);
    },

    async detail(auctionId: string): Promise<AuctionDetail> {
      return parsed(await request('GET', `/auctions/${auctionId}`), parseAuctionDetail);
    },

    async history(auctionId: string, before?: number, limit = 20): Promise<BidHistoryPage> {
      const query = new URLSearchParams({ limit: String(limit) });
      if (before !== undefined) query.set('before', String(before));
      return parsed(await request('GET', `/auctions/${auctionId}/bids?${query}`), parseBidHistoryPage);
    },

    async me(): Promise<UserProfile> {
      return parsed(await request('GET', '/users/me', { auth: 'required' }), parseUserProfile);
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

function parsed<T>(body: unknown, parse: (v: unknown) => T | null): T {
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
