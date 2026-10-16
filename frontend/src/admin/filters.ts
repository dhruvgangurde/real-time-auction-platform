// The users list's filters in the URL (FR-76): the API's own bounds (admin.py list_users: q at most
// 200, role and status from their lists, page 1 to 10 000), so a hand-edited link never gets a 422.
const ROLES = ['bidder', 'seller', 'admin'];
const STATUSES = ['pending_verification', 'active', 'suspended'];
export const PAGE_SIZE = 20;

export function usersQuery(search: URLSearchParams): { q: string; role: string; status: string; page: number; api: string } {
  const q = (search.get('q') ?? '').trim().slice(0, 200);
  const role = ROLES.includes(search.get('role') ?? '') ? (search.get('role') as string) : '';
  const status = STATUSES.includes(search.get('status') ?? '') ? (search.get('status') as string) : '';
  const asked = Number(search.get('page'));
  const page = Number.isInteger(asked) && asked >= 1 ? Math.min(asked, 10_000) : 1;
  const api = new URLSearchParams();
  if (q !== '') api.set('q', q);
  if (role !== '') api.set('role', role);
  if (status !== '') api.set('status', status);
  api.set('page', String(page));
  api.set('page_size', String(PAGE_SIZE));
  return { q, role, status, page, api: api.toString() };
}
