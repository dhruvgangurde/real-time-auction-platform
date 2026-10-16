// The administrator's calls (FR-63, FR-75, FR-76, FR-80, FR-20) on the shared client; they live in
// the admin chunk, so the entry does not carry them. Every one needs the admin role on the server
// (403 FORBIDDEN otherwise) and every change a reason, written to the audit log (BR-12).
import type { Api } from '../api/client';

export async function forceClose(api: Api, auctionId: string, reason: string): Promise<void> {
  await api.request('POST', `/admin/auctions/${auctionId}/force-close`, { auth: 'required', body: { reason } });
}

export async function cancelAuction(api: Api, auctionId: string, reason: string): Promise<void> {
  await api.request('POST', `/admin/auctions/${auctionId}/cancel`, { auth: 'required', body: { reason } });
}
