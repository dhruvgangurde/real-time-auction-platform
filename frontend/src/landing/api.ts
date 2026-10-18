// The landing's REST calls (brief 8.1, 8.3, 8.5). Every one is anonymous, so a signed-in visitor
// gets the same cached public bodies, and no socket is opened on this page (FR-28). Every one goes
// through the page's one scheduler (scheduler.ts), so their total rate stays under the anonymous
// per-address limit.
import { REQUESTED_WITH } from '../api/auth';
import { parsed, type Api } from '../api/client';
import { parseAuctionDetail, parseAuctionPage, parseBidHistoryPage, type AuctionCard, type AuctionDetail, type BidHistoryPage } from '../lib/contract';
import { parseDemoOverview, parseStressStart, parseStressStatus, type DemoOverview, type StressStart, type StressStatus } from './contract';
import type { Priority, Scheduler } from './scheduler';

export const LEDGER_PAGE = 100; // the bid history's largest page (api/discovery.py)
export const FEATURED = 7; // brief 8.5: five to seven lots

export type LandingApi = ReturnType<typeof landingApi>;

export function landingApi(api: Pick<Api, 'request'>, scheduler: Scheduler, cancelled: () => boolean = () => false) {
  const call = (method: string, path: string, priority: Priority, headers?: Record<string, string>) =>
    scheduler.run(() => api.request(method, path, { auth: 'none', ...(headers ? { headers } : {}) }), priority, cancelled);

  return {
    /** null: demo mode is off (GET /demo answers {"enabled": false}, brief 12.3) */
    async overview(): Promise<DemoOverview | null> {
      const overview = parsed(await call('GET', '/demo', 'high'), parseDemoOverview);
      return overview.enabled ? overview : null;
    },
    async startStress(): Promise<StressStart> {
      return parsed(await call('POST', '/demo/stress', 'high', { 'X-Requested-With': REQUESTED_WITH }), parseStressStart);
    },
    async stressStatus(runId: string): Promise<StressStatus> {
      return parsed(await call('GET', `/demo/stress/${runId}`, 'high'), parseStressStatus);
    },
    async detail(auctionId: string): Promise<AuctionDetail> {
      return parsed(await call('GET', `/auctions/${auctionId}`, 'high'), parseAuctionDetail);
    },
    /** one page of the ledger, newest first; `before` is the previous page's next_before */
    async ledgerPage(auctionId: string, before?: number): Promise<BidHistoryPage> {
      const query = new URLSearchParams({ limit: String(LEDGER_PAGE) });
      if (before !== undefined) query.set('before', String(before));
      return parsed(await call('GET', `/auctions/${auctionId}/bids?${query}`, 'low'), parseBidHistoryPage);
    },
    /** the newest live real lot: demo lots are never listed (repositories/auction.py) */
    async newestLive(): Promise<AuctionCard | null> {
      const page = parsed(await call('GET', '/auctions?status=live&page_size=1', 'low'), parseAuctionPage);
      return page.items[0] ?? null;
    },
    async featured(): Promise<AuctionCard[]> {
      const query = `/auctions?status=live&status=scheduled&page_size=${FEATURED}`;
      return parsed(await call('GET', query, 'low'), parseAuctionPage).items;
    },
  };
}
