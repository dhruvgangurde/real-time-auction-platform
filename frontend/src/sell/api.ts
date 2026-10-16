// The seller's calls (FR-11 to FR-19) on the shared client; they live in the seller chunk, so the
// entry does not carry them. The server checks the seller role and ownership on every one.
import { parsed, type Api } from '../api/client';
import { parseAuctionPage, type AuctionPage } from '../lib/contract';

const created = (v: unknown) => {
  const id = (v as { id?: unknown } | null)?.id;
  return typeof id === 'string' ? id : null;
};

export async function sellerAuctions(api: Api, page: number): Promise<AuctionPage> {
  return parsed(await api.request('GET', `/seller/auctions?page=${page}`, { auth: 'required' }), parseAuctionPage);
}

/** Only the changed fields (FR-17); the server re-checks what the status allows. */
export async function updateListing(api: Api, id: string, body: Record<string, unknown>): Promise<void> {
  await api.request('PATCH', `/auctions/${id}`, { auth: 'required', body });
}

/** A photo: the file itself is the body (D10); the server decodes, checks and re-encodes it. */
export async function uploadImage(api: Api, id: string, file: File): Promise<void> {
  await api.request('POST', `/auctions/${id}/images`, { auth: 'required', body: file });
}

export async function deleteImage(api: Api, id: string, imageId: number): Promise<void> {
  await api.request('DELETE', `/auctions/${id}/images/${imageId}`, { auth: 'required' });
}

/** A new draft (FR-11); its id. Ten per hour per seller (NFR-22). */
export async function createListing(api: Api, body: Record<string, unknown>): Promise<string> {
  return parsed(await api.request('POST', '/auctions', { auth: 'required', body }), created);
}
