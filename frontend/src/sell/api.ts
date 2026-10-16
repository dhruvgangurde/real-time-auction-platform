// The seller's calls (FR-11 to FR-19) on the shared client; they live in the seller chunk, so the
// entry does not carry them. The server checks the seller role and ownership on every one.
import { parsed, type Api } from '../api/client';
import { parseAuctionPage, type AuctionPage } from '../lib/contract';

export async function sellerAuctions(api: Api, page: number): Promise<AuctionPage> {
  return parsed(await api.request('GET', `/seller/auctions?page=${page}`, { auth: 'required' }), parseAuctionPage);
}
