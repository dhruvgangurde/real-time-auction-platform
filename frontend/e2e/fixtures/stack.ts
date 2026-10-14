// Test data the way a person makes it: through the public API on 8011 and the Mailpit sink on
// 8025, roles through the CLI (D6: roles only via CLI, which goes through the services and the
// audit log). No table is written directly. Every spec in this folder builds on these.
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { expect, type APIRequestContext, type APIResponse } from '@playwright/test';

export const API = 'http://localhost:8011/api/v1';
const MAILPIT = 'http://localhost:8025/api/v1';
export const ROOT = fileURLToPath(new URL('../../..', import.meta.url));
export const IMAGE = fileURLToPath(new URL('../../../design/mockups/images/pentax-k1000.jpg', import.meta.url));

export type Role = 'seller' | 'admin';
export type Account = { email: string; password: string; name: string; id: string; token: string };

const tag = () => Math.random().toString(36).slice(2, 8);

async function ok(call: Promise<APIResponse>): Promise<APIResponse> {
  const r = await call;
  if (!r.ok()) throw new Error(`${r.url()} -> ${r.status()}: ${await r.text()}`);
  return r;
}

export const bearer = (a: Account) => ({ authorization: `Bearer ${a.token}` });

/** The room's socket is subscribed: from here socket-only frames (an outbid notice) reach the page.
 * The bid field renders before that, so a spec that acts on such frames waits for this first. */
export async function waitForLive(page: import('@playwright/test').Page): Promise<void> {
  await expect(page.getByText(/^Connected, /)).toBeVisible({ timeout: 15_000 });
}

/** Sign in through the SPA so the browser context holds the app's own session and socket. */
export async function signInUi(page: import('@playwright/test').Page, account: Account): Promise<void> {
  await page.goto('/sign-in');
  await page.getByLabel('Email').fill(account.email);
  await page.getByLabel('Password').fill(account.password);
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await page.waitForURL('http://localhost:8011/');
}

/** The token from the newest Mailpit message to this address whose text links to `path`. */
export async function mailToken(api: APIRequestContext, email: string, path: 'verify-email' | 'reset-password'): Promise<string> {
  const pattern = new RegExp(`/${path}#token=([A-Za-z0-9_-]+)`);
  for (let i = 0; i < 60; i += 1) {
    const list = await (await api.get(`${MAILPIT}/search?query=${encodeURIComponent(`to:"${email}"`)}`)).json();
    for (const m of list.messages as { ID: string }[]) {
      const full = await (await api.get(`${MAILPIT}/message/${m.ID}`)).json();
      const match = pattern.exec(String(full.Text ?? ''));
      if (match?.[1]) return match[1];
    }
    await new Promise((r) => setTimeout(r, 250));
  }
  throw new Error(`no ${path} mail for ${email}`);
}

/** Register, verify from the mail, grant the role (CLI), log in. */
export async function newAccount(api: APIRequestContext, name: string, role?: Role): Promise<Account> {
  const t = tag();
  const email = `e2e-${name}-${t}@example.com`;
  const password = `Pw-${t}-${tag()}-x1`;
  const reg = await ok(api.post(`${API}/auth/register`, { data: { email, password, full_name: name } }));
  const id = String(((await reg.json()) as { id: string }).id);
  await ok(api.post(`${API}/auth/verify-email`, { data: { token: await mailToken(api, email, 'verify-email') } }));
  if (role) grantRole(email, role);
  const token = await login(api, email, password);
  return { email, password, name, id, token };
}

/** Reset an account's password through the mail link: every session is revoked (socket 4004). */
export async function resetPassword(api: APIRequestContext, account: Account): Promise<string> {
  await ok(api.post(`${API}/auth/forgot-password`, { data: { email: account.email } }));
  const token = await mailToken(api, account.email, 'reset-password');
  const password = `Pw-${tag()}-${tag()}-reset`;
  await ok(api.post(`${API}/auth/reset-password`, { data: { token, password } }));
  return password;
}

export async function login(api: APIRequestContext, email: string, password: string): Promise<string> {
  const r = await ok(api.post(`${API}/auth/login`, { data: { email, password } }));
  return String(((await r.json()) as { access_token: string }).access_token);
}

export function grantRole(email: string, role: Role): void {
  execFileSync('docker', ['compose', 'exec', '-T', 'api', 'python', '-m', 'app.cli', 'grant-role', email, role], { cwd: ROOT, stdio: 'pipe' });
}

/** The web service's log since an RFC3339 instant: for the token-in-log check (Amendment 1). */
export function webLogsSince(since: string): string {
  return execFileSync('docker', ['compose', 'logs', 'web', '--since', since], { cwd: ROOT }).toString();
}

/** Record every token a browser request carries to a server (url, headers, non-auth body). */
export function watchTokenLeaks(page: import('@playwright/test').Page, tokens: () => string[]): string[] {
  const leaks: string[] = [];
  page.on('request', (req) => {
    const u = new URL(req.url());
    const carrier = u.pathname + u.search; // a fragment is never sent to a server
    const headers = JSON.stringify(req.headers());
    const body = req.postData() ?? '';
    const authBody = /\/auth\/(verify-email|reset-password)$/.test(u.pathname);
    for (const token of tokens()) {
      if (token === '') continue;
      if (carrier.includes(token)) leaks.push(`url ${carrier}`);
      if (headers.includes(token)) leaks.push(`header ${u.pathname}`);
      if (!authBody && body.includes(token)) leaks.push(`body ${u.pathname}`);
    }
  });
  return leaks;
}

export type AuctionSpec = {
  title?: string;
  startInS?: number;
  endInS?: number;
  startingPrice?: number;
  minIncrement?: number;
  /** null for no reserve */
  reservePrice?: number | null;
  thresholdS?: number;
  extensionS?: number;
  publish?: boolean;
};

/** A listing by `seller` with one photo, published unless told otherwise; returns its id. */
export async function newAuction(api: APIRequestContext, seller: Account, spec: AuctionSpec = {}): Promise<string> {
  const now = Date.now();
  const body: Record<string, unknown> = {
    title: spec.title ?? 'Asahi Pentax K1000 35 mm SLR with 50 mm f/2',
    description: 'Fully mechanical film SLR with standard lens. Light wear to the top plate; shutter fires at all speeds, meter not checked.',
    category_id: 1,
    starting_price: spec.startingPrice ?? 900000,
    min_increment: spec.minIncrement ?? 50000,
    start_time: new Date(now + (spec.startInS ?? 8) * 1000).toISOString(),
    end_time: new Date(now + (spec.endInS ?? 75) * 1000).toISOString(),
    antisnipe_threshold_s: spec.thresholdS ?? 20,
    antisnipe_extension_s: spec.extensionS ?? 20,
  };
  if (spec.reservePrice !== null) body.reserve_price = spec.reservePrice ?? 1025000;
  const created = await ok(api.post(`${API}/auctions`, { headers: bearer(seller), data: body }));
  const id = String(((await created.json()) as { id: string }).id);
  await ok(api.post(`${API}/auctions/${id}/images`, { headers: { ...bearer(seller), 'content-type': 'image/jpeg' }, data: readFileSync(IMAGE) }));
  if (spec.publish !== false) await ok(api.post(`${API}/auctions/${id}/publish`, { headers: bearer(seller) }));
  return id;
}

export async function live(api: APIRequestContext, id: string, account?: Account): Promise<Record<string, unknown>> {
  return (await ok(api.get(`${API}/auctions/${id}/live`, account ? { headers: bearer(account) } : {}))).json() as Promise<Record<string, unknown>>;
}

/** Until the scheduler has taken the auction live (within 5 s of start_time). */
export async function waitForStatus(api: APIRequestContext, id: string, status: string, timeoutMs = 40_000): Promise<void> {
  const until = Date.now() + timeoutMs;
  while (Date.now() < until) {
    if ((await live(api, id)).status === status) return;
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error(`auction ${id} never reached ${status}`);
}

export async function restBid(api: APIRequestContext, account: Account, id: string, amount: number): Promise<APIResponse> {
  return api.post(`${API}/auctions/${id}/bids`, { headers: bearer(account), data: { amount, request_id: `e2e-${tag()}${tag()}` } });
}

export async function restProxy(api: APIRequestContext, account: Account, id: string, maxAmount: number): Promise<APIResponse> {
  return api.post(`${API}/auctions/${id}/proxy`, { headers: bearer(account), data: { max_amount: maxAmount, request_id: `e2e-${tag()}${tag()}` } });
}

/** Wait until the auction is within its antisnipe threshold of the end, then bid to extend it. */
export async function bidInLastWindow(api: APIRequestContext, account: Account, id: string, amount: number, thresholdS: number): Promise<void> {
  const until = Date.now() + 120_000;
  while (Date.now() < until) {
    const s = await live(api, id, account); // authenticated: fresh, bypasses the 1 s anonymous cache
    const remaining = Number(s.end_time_ms) - Number(s.server_time_ms);
    if (s.status !== 'live') throw new Error(`auction ${id} is ${String(s.status)}, not live`);
    if (remaining <= thresholdS * 1000) {
      const r = await restBid(api, account, id, amount);
      if (!r.ok()) throw new Error(`extend bid -> ${r.status()}: ${await r.text()}`);
      return;
    }
    await new Promise((res) => setTimeout(res, 1000));
  }
  throw new Error(`auction ${id} never entered its last ${thresholdS}s`);
}
