// The administrator's calls (FR-63, FR-75, FR-76, FR-80, FR-20) on the shared client; they live in
// the admin chunk, so the entry does not carry them. Every one needs the admin role on the server
// (403 FORBIDDEN otherwise) and every change a reason, written to the audit log (BR-12).
import { parsed, type Api } from '../api/client';

export type Role = 'bidder' | 'seller' | 'admin';
export type UserStatus = 'pending_verification' | 'active' | 'suspended';
export type AdminUser = { id: string; email: string; full_name: string; status: UserStatus; roles: Role[]; created_at_ms: number };
export type UserPage = { items: AdminUser[]; page: number; page_size: number; has_more: boolean };

const ROLES = new Set(['bidder', 'seller', 'admin']);
const STATUSES = new Set(['pending_verification', 'active', 'suspended']);

function user(v: unknown): AdminUser | null {
  const u = v as Partial<Record<keyof AdminUser, unknown>> | null;
  if (u === null || typeof u !== 'object') return null;
  const { id, email, full_name, status, roles, created_at_ms } = u;
  if (typeof id !== 'string' || typeof email !== 'string' || typeof full_name !== 'string' || typeof created_at_ms !== 'number') return null;
  if (typeof status !== 'string' || !STATUSES.has(status) || !Array.isArray(roles) || !roles.every((r) => typeof r === 'string' && ROLES.has(r))) return null;
  return { id, email, full_name, status: status as UserStatus, roles: roles as Role[], created_at_ms };
}

function userPage(v: unknown): UserPage | null {
  const p = v as { items?: unknown; page?: unknown; page_size?: unknown; has_more?: unknown } | null;
  if (p === null || !Array.isArray(p.items) || typeof p.page !== 'number' || typeof p.page_size !== 'number' || typeof p.has_more !== 'boolean') return null;
  const items = p.items.map(user);
  return items.every((u): u is AdminUser => u !== null) ? { items, page: p.page, page_size: p.page_size, has_more: p.has_more } : null;
}

/** FR-76: suspension ends every session of the account; reinstatement lets it sign in again. */
export async function suspendUser(api: Api, id: string, reason: string): Promise<void> {
  await api.request('POST', `/admin/users/${id}/suspend`, { auth: 'required', body: { reason } });
}

export async function reinstateUser(api: Api, id: string, reason: string): Promise<void> {
  await api.request('POST', `/admin/users/${id}/reinstate`, { auth: 'required', body: { reason } });
}

/** A revoke ends every session of the account, so it applies at once (decisions 2026-10-13). */
export async function setRole(api: Api, id: string, role: Role, grant: boolean, reason: string): Promise<void> {
  await api.request(grant ? 'PUT' : 'DELETE', `/admin/users/${id}/roles/${role}`, { auth: 'required', body: { reason } });
}

export type AuditEntry = {
  id: number;
  actor_id: string | null;
  entity_type: string;
  entity_id: string;
  action: string;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  reason: string | null;
  created_at_ms: number;
};
export type AuditPage = { items: AuditEntry[]; next_before_id: number | null };

const record = (v: unknown): v is Record<string, unknown> | null => v === null || (typeof v === 'object' && !Array.isArray(v));
export function auditEntry(v: unknown): AuditEntry | null {
  const e = v as Partial<Record<keyof AuditEntry, unknown>> | null;
  if (e === null || typeof e !== 'object') return null;
  const { id, actor_id, entity_type, entity_id, action, before, after, reason, created_at_ms } = e;
  if (typeof id !== 'number' || (actor_id !== null && typeof actor_id !== 'string') || typeof entity_type !== 'string' || typeof entity_id !== 'string') return null;
  if (typeof action !== 'string' || !record(before) || !record(after) || (reason !== null && typeof reason !== 'string') || typeof created_at_ms !== 'number') return null;
  return { id, actor_id, entity_type, entity_id, action, before, after, reason, created_at_ms };
}
function auditPage(v: unknown): AuditPage | null {
  const p = v as { items?: unknown; next_before_id?: unknown } | null;
  if (p === null || !Array.isArray(p.items) || (p.next_before_id !== null && typeof p.next_before_id !== 'number')) return null;
  const items = p.items.map(auditEntry);
  return items.every((x): x is AuditEntry => x !== null) ? { items, next_before_id: p.next_before_id } : null;
}

/** FR-80: newest first, keyset on the entry id. */
export async function auditLog(api: Api, query: string, beforeId?: number): Promise<AuditPage> {
  const q = new URLSearchParams(query);
  if (beforeId !== undefined) q.set('before_id', String(beforeId));
  return parsed(await api.request('GET', `/admin/audit?${q}`, { auth: 'required' }), auditPage);
}

/** FR-20: categories change only by an administrator, each with a reason (FR-75). */
export async function createCategory(api: Api, body: { name: string; slug: string; parent_id: number | null; reason: string }): Promise<void> {
  await api.request('POST', '/categories', { auth: 'required', body });
}

export async function updateCategory(api: Api, id: number, body: Record<string, unknown>): Promise<void> {
  await api.request('PATCH', `/categories/${id}`, { auth: 'required', body });
}

export async function deleteCategory(api: Api, id: number, reason: string): Promise<void> {
  await api.request('DELETE', `/categories/${id}`, { auth: 'required', body: { reason } });
}

/** FR-76: by name or email, role and status; newest first. */
export async function listUsers(api: Api, query: URLSearchParams): Promise<UserPage> {
  return parsed(await api.request('GET', `/admin/users?${query}`, { auth: 'required' }), userPage);
}

export async function forceClose(api: Api, auctionId: string, reason: string): Promise<void> {
  await api.request('POST', `/admin/auctions/${auctionId}/force-close`, { auth: 'required', body: { reason } });
}

export async function cancelAuction(api: Api, auctionId: string, reason: string): Promise<void> {
  await api.request('POST', `/admin/auctions/${auctionId}/cancel`, { auth: 'required', body: { reason } });
}
