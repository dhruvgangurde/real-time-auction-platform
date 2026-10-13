// Screenshots of the built room for review against design/shots/03-room (brief 14 item 2). Every
// frame is a real state driven through the API and the UI on 8011. PNGs are git-ignored
// (design/shots/**/*.png); this spec regenerates them and writes index.md. States that are
// transient or unreachable in the real backend are listed in index.md, not shot (see the notes).
import { execFileSync } from 'node:child_process';
import { mkdirSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { expect, test, type Page } from '@playwright/test';

import { enIN } from '../src/strings/en-IN';
import { ROOT, bidInLastWindow, newAccount, newAuction, restBid, signInUi, waitForStatus } from './fixtures/stack';

const OUT = fileURLToPath(new URL('../../design/shots/04-room-built', import.meta.url));
mkdirSync(OUT, { recursive: true });

type Row = { state: string; file: string; theme: string; polite: string; assertive: string; note?: string };
const rows: Row[] = [];

async function shoot(page: Page, state: string, theme: 'light' | 'dark', note?: string): Promise<void> {
  const polite = (await page.locator('#live-polite').textContent()) ?? '';
  const assertive = (await page.locator('#live-assertive').textContent()) ?? '';
  for (const width of [1440, 390] as const) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 900 });
    await page.waitForTimeout(150);
    const file = `${state}-${width}-${theme}.png`;
    await page.screenshot({ path: `${OUT}/${file}`, fullPage: true });
    rows.push({ state, file, theme, polite, assertive, ...(note ? { note } : {}) });
  }
  await page.setViewportSize({ width: 1440, height: 900 });
}

const field = (page: Page) => page.getByLabel(enIN.roomUi.bid.fieldLabel);

test.describe.configure({ mode: 'serial' });

test('live family, light: idle, rejected, accepted, outbid, proxy', async ({ browser, request }) => {
  test.setTimeout(180_000);
  const seller = await newAccount(request, 'seller', 'seller');
  const alice = await newAccount(request, 'alice');
  const bob = await newAccount(request, 'bob');
  const id = await newAuction(request, seller, { endInS: 600, startingPrice: 900000, minIncrement: 50000, reservePrice: 950000 });
  await waitForStatus(request, id, 'live');
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: 'light' });
  const page = await ctx.newPage();
  await signInUi(page, alice);
  await page.goto(`/auctions/${id}`);
  await expect(field(page)).toBeVisible();
  await shoot(page, '04-idle', 'light');

  await field(page).fill('8,000');
  await page.keyboard.press('Enter');
  await expect(page.locator('#live-assertive')).toContainText('Bid not placed', { timeout: 10_000 });
  await shoot(page, '07-rejected', 'light');

  await field(page).fill('9,000');
  await page.keyboard.press('Enter');
  await expect(page.locator('#live-assertive')).toHaveText(enIN.room.accepted('₹9,000'), { timeout: 10_000 });
  await shoot(page, '06-accepted', 'light');

  await (await restBid(request, bob, id, 950000)).json();
  await expect(page.locator('#live-assertive')).toContainText('been outbid', { timeout: 10_000 });
  await shoot(page, '10-outbid', 'light');

  await page.getByRole('button', { name: enIN.roomUi.proxy.setAction, exact: true }).click();
  await page.getByLabel(enIN.roomUi.proxy.fieldLabel).fill('15,000');
  await page.getByRole('button', { name: enIN.roomUi.proxy.confirm }).click();
  await expect(page.getByText(enIN.roomUi.proxy.only)).toBeVisible({ timeout: 10_000 });
  await shoot(page, '11-proxy-active', 'light');

  // reserve met once alice's proxy answered above ₹9,500
  await expect(page.getByText(/Reserve met/)).toBeVisible();
  await shoot(page, '19-reserve-met', 'light');
  await ctx.close();
});

test('dark: idle, outbid, extending', async ({ browser, request }) => {
  test.setTimeout(180_000);
  const seller = await newAccount(request, 'seller', 'seller');
  const alice = await newAccount(request, 'alice');
  const bob = await newAccount(request, 'bob');
  const id = await newAuction(request, seller, { endInS: 70, thresholdS: 20, extensionS: 20, startingPrice: 900000, minIncrement: 50000, reservePrice: null });
  await waitForStatus(request, id, 'live');
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: 'dark' });
  const page = await ctx.newPage();
  await signInUi(page, alice);
  await page.goto(`/auctions/${id}`);
  await expect(field(page)).toBeVisible();
  await shoot(page, '04-idle', 'dark');

  await (await restBid(request, alice, id, 900000)).json();
  await (await restBid(request, bob, id, 950000)).json();
  await expect(page.locator('#live-assertive')).toContainText('been outbid', { timeout: 10_000 });
  await shoot(page, '10-outbid', 'dark');

  await bidInLastWindow(request, alice, id, 1000000, 20);
  await expect(page.locator('#live-polite')).toContainText('Extended by', { timeout: 15_000 });
  await shoot(page, '13-extending', 'dark');
  await ctx.close();
});

test('extending, light', async ({ browser, request }) => {
  test.setTimeout(120_000);
  const seller = await newAccount(request, 'seller', 'seller');
  const alice = await newAccount(request, 'alice');
  const bob = await newAccount(request, 'bob');
  const id = await newAuction(request, seller, { endInS: 70, thresholdS: 20, extensionS: 20, startingPrice: 900000, minIncrement: 50000, reservePrice: null });
  await waitForStatus(request, id, 'live');
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: 'light' });
  const page = await ctx.newPage();
  await signInUi(page, alice);
  await page.goto(`/auctions/${id}`);
  await (await restBid(request, alice, id, 900000)).json();
  await bidInLastWindow(request, bob, id, 950000, 20);
  await expect(page.locator('#live-polite')).toContainText('Extended by', { timeout: 15_000 });
  await shoot(page, '13-extending', 'light');
  await ctx.close();
});

test('terminal: closed-won, closed-lost, no-sale, cancelled', async ({ browser, request }) => {
  test.setTimeout(200_000);
  const seller = await newAccount(request, 'seller', 'seller');
  const admin = await newAccount(request, 'admin', 'admin');
  const alice = await newAccount(request, 'alice');
  const bob = await newAccount(request, 'bob');

  // a sold auction, met reserve: alice wins, bob loses
  const sold = await newAuction(request, seller, { endInS: 600, reservePrice: 1025000 });
  await waitForStatus(request, sold, 'live');
  await (await restBid(request, bob, sold, 1000000)).json();
  await (await restBid(request, alice, sold, 1100000)).json(); // alice leads, reserve met
  await execFileSync('docker', ['compose', 'exec', '-T', 'api', 'python', '-m', 'app.cli', 'grant-role', admin.email, 'admin'], { cwd: ROOT, stdio: 'pipe' });

  const aliceCtx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const ap = await aliceCtx.newPage();
  await signInUi(ap, alice);
  await ap.goto(`/auctions/${sold}`);
  const bobCtx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const bp = await bobCtx.newPage();
  await signInUi(bp, bob);
  await bp.goto(`/auctions/${sold}`);
  // force-close via the admin API to settle now
  await request.post(`http://localhost:8011/api/v1/admin/auctions/${sold}/force-close`, { headers: { authorization: `Bearer ${admin.token}` }, data: { reason: 'shot: settle the sold auction' } });
  await waitForStatus(request, sold, 'closed_sold', 60_000);
  await expect(ap.getByText(enIN.roomUi.price.wonAt, { exact: true })).toBeVisible({ timeout: 15_000 });
  await shoot(ap, '16-closed-won', 'light');
  await expect(bp.getByText(enIN.roomUi.price.soldFor, { exact: true })).toBeVisible({ timeout: 15_000 });
  await shoot(bp, '17-closed-lost', 'light');
  await aliceCtx.close();
  await bobCtx.close();

  // a no-sale: one bid below reserve, force-closed
  const nosale = await newAuction(request, seller, { endInS: 600, reservePrice: 5000000 });
  await waitForStatus(request, nosale, 'live');
  await (await restBid(request, bob, nosale, 900000)).json();
  const nc = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const np = await nc.newPage();
  await signInUi(np, alice);
  await np.goto(`/auctions/${nosale}`);
  await request.post(`http://localhost:8011/api/v1/admin/auctions/${nosale}/force-close`, { headers: { authorization: `Bearer ${admin.token}` }, data: { reason: 'shot: no sale' } });
  await waitForStatus(request, nosale, 'closed_no_sale', 60_000);
  await expect(np.getByText(enIN.roomUi.price.highest, { exact: true })).toBeVisible({ timeout: 15_000 });
  await shoot(np, '18-no-sale', 'light');
  await nc.close();

  // a cancelled auction
  const cancelled = await newAuction(request, seller, { endInS: 600 });
  await waitForStatus(request, cancelled, 'live');
  await (await restBid(request, bob, cancelled, 900000)).json();
  const cc = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const cp = await cc.newPage();
  await signInUi(cp, alice);
  await cp.goto(`/auctions/${cancelled}`);
  await request.post(`http://localhost:8011/api/v1/admin/auctions/${cancelled}/cancel`, { headers: { authorization: `Bearer ${admin.token}` }, data: { reason: 'shot: cancelled by the platform' } });
  await waitForStatus(request, cancelled, 'cancelled', 60_000);
  await expect(cp.getByText(enIN.roomUi.price.lastBid, { exact: true })).toBeVisible({ timeout: 15_000 });
  await shoot(cp, '20-cancelled', 'light');
  await cc.close();
});

test('roles and misc: anonymous, scheduled, draft, seller, admin, other-tab', async ({ browser, request }) => {
  test.setTimeout(200_000);
  const seller = await newAccount(request, 'seller', 'seller');
  const admin = await newAccount(request, 'admin', 'admin');
  const alice = await newAccount(request, 'alice');
  const bob = await newAccount(request, 'bob');
  const id = await newAuction(request, seller, { endInS: 600, reservePrice: 1025000 });
  await waitForStatus(request, id, 'live');
  await (await restBid(request, bob, id, 900000)).json();

  // anonymous: no session, polling, "Sign in to bid"
  const anon = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const anonPage = await anon.newPage();
  await anonPage.goto(`/auctions/${id}`);
  await expect(anonPage.getByRole('link', { name: enIN.roomUi.bid.signInToBid })).toBeVisible();
  await shoot(anonPage, '01-anonymous', 'light');
  await anon.close();

  // seller's own auction: the reserve, owner-only
  const sc = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const sp = await sc.newPage();
  await signInUi(sp, seller);
  await sp.goto(`/auctions/${id}`);
  await expect(sp.getByText(enIN.roomUi.seller.reserveBadge)).toBeVisible();
  await shoot(sp, '28-seller', 'light');
  await sc.close();

  // admin: the force-close dialog open with a reason required
  const adc = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const adp = await adc.newPage();
  await signInUi(adp, admin);
  await adp.goto(`/auctions/${id}`);
  await adp.getByRole('button', { name: enIN.roomUi.admin.forceClose, exact: true }).click();
  await expect(adp.getByRole('dialog')).toBeVisible();
  await adp.getByLabel(enIN.roomUi.admin.reasonLabel).fill('shot: the reason is written to the audit log');
  await shoot(adp, '29-admin-force-close', 'light');
  await adc.close();

  // scheduled and draft: an auction that has not opened
  const future = await newAuction(request, seller, { startInS: 3600, endInS: 7200, reservePrice: 1025000, publish: true });
  const fc = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const fp = await fc.newPage();
  await signInUi(fp, alice);
  await fp.goto(`/auctions/${future}`);
  await expect(fp.getByText(/Bidding opens at/).first()).toBeVisible({ timeout: 10_000 });
  await shoot(fp, '02-scheduled', 'light');
  await fc.close();

  const draft = await newAuction(request, seller, { startInS: 3600, endInS: 7200, publish: false });
  const dc = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const dp = await dc.newPage();
  await signInUi(dp, seller);
  await dp.goto(`/auctions/${draft}`);
  await expect(dp.getByText(enIN.roomUi.status.draft)).toBeVisible({ timeout: 10_000 });
  await shoot(dp, '30-draft', 'light');
  await dc.close();

  // live in another tab: a second tab of the same context steals the socket
  const tc = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const t1 = await tc.newPage();
  await signInUi(t1, alice);
  await t1.goto(`/auctions/${id}`);
  const t2 = await tc.newPage();
  await t2.goto(`/auctions/${id}`);
  // one tab holds the socket; the other polls and is offered "Use this tab" (which one wins the
  // Web Lock is timing-dependent, so shoot whichever shows it)
  const btn1 = t1.getByRole('button', { name: enIN.roomUi.status.useThisTab });
  const btn2 = t2.getByRole('button', { name: enIN.roomUi.status.useThisTab });
  await expect(async () => {
    const [c1, c2] = await Promise.all([btn1.count(), btn2.count()]);
    expect(c1 + c2).toBeGreaterThan(0);
  }).toPass({ timeout: 15_000 });
  await shoot((await btn1.count()) > 0 ? t1 : t2, '23-other-tab', 'light');
  await tc.close();

});

test('degraded, then recover (Redis paused); light and dark', async ({ browser, request }) => {
  test.setTimeout(180_000);
  const seller = await newAccount(request, 'seller', 'seller');
  const alice = await newAccount(request, 'alice');
  const bob = await newAccount(request, 'bob');
  const id = await newAuction(request, seller, { endInS: 600, reservePrice: null });
  await waitForStatus(request, id, 'live');
  await (await restBid(request, bob, id, 900000)).json();

  for (const theme of ['light', 'dark'] as const) {
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: theme });
    const page = await ctx.newPage();
    await signInUi(page, alice);
    await page.goto(`/auctions/${id}`);
    await expect(field(page)).toBeVisible();
    execFileSync('docker', ['compose', 'pause', 'redis'], { cwd: ROOT, stdio: 'pipe' });
    try {
      await expect(page.getByText(enIN.roomUi.banner.degraded).first()).toBeVisible({ timeout: 20_000 });
      await shoot(page, '21-degraded', theme);
    } finally {
      execFileSync('docker', ['compose', 'unpause', 'redis'], { cwd: ROOT, stdio: 'pipe' });
    }
    await ctx.close();
  }

  // the amendment: after unpause the readiness probe passes and the close task found nothing unresolved
  const until = Date.now() + 60_000;
  let ready = false;
  while (Date.now() < until) {
    if ((await request.get('http://localhost:8011/api/v1/health/ready')).status() === 200) {
      ready = true;
      break;
    }
    await new Promise((r) => setTimeout(r, 1000));
  }
  expect(ready).toBe(true);
  await waitForStatus(request, id, 'live', 30_000); // still live, rebuilt from Postgres
  const logs = execFileSync('docker', ['compose', 'logs', 'worker', '--since', '2m'], { cwd: ROOT }).toString();
  expect(logs).not.toContain('UNRESOLVED_CLOSE');
});

test.afterAll(async () => {
  const order = ['01-anonymous', '02-scheduled', '04-idle', '06-accepted', '07-rejected', '10-outbid', '11-proxy-active', '13-extending', '16-closed-won', '17-closed-lost', '18-no-sale', '19-reserve-met', '20-cancelled', '21-degraded', '23-other-tab', '28-seller', '29-admin-force-close', '30-draft'];
  rows.sort((a, b) => order.indexOf(a.state) - order.indexOf(b.state) || a.theme.localeCompare(b.theme) || a.file.localeCompare(b.file));
  const esc = (s: string) => s.replace(/\|/g, '\\|').trim();
  const lines = [
    '# 04-room-built shots',
    '',
    'The auction room as built (`frontend/src/room/`), rendered from real state on 8011 by `frontend/e2e/room-shots.spec.ts`, for review against `design/shots/03-room`. Every frame is a real state driven through the API and the UI, not a mock. PNGs are git-ignored (`design/shots/**/*.png`); re-make them with `cd frontend && npx playwright test e2e/room-shots.spec.ts`.',
    '',
    'Each row is the state, the file, and the live-region text observed at the moment of the shot (polite for room-wide changes, assertive for the viewer\'s own outcomes). Light at 1440 and 390; idle, outbid, extending and degraded also in dark.',
    '',
    '| State | File | Live regions |',
    '|---|---|---|',
    ...rows.map((r) => {
      const live = [r.polite ? `Polite: "${esc(r.polite)}"` : '', r.assertive ? `Assertive: "${esc(r.assertive)}"` : ''].filter(Boolean).join(' ') || 'Nothing announced.';
      return `| ${r.state}${r.note ? ` (${esc(r.note)})` : ''} | ${r.file} | ${live} |`;
    }),
    '',
    '## Not shot here',
    '',
    '- **Pending, accepted-tick, closing** are sub-second transitions; the two-bidder, keyboard and mobile specs exercise them in motion.',
    '- **Retrying and reconnecting** need a mid-flight Redis or socket drop; the degraded shot covers the fail-closed banner, and `room.test.ts` covers the retry reducer path.',
    '- **Signed out elsewhere (4004)** needs another device to revoke the session mid-view.',
    '- **Not verified** is unreachable in the room: the backend refuses login until the email is verified (`EMAIL_NOT_VERIFIED`), so a signed-in viewer is always verified. The sign-in screen carries that state instead (`e2e/auth.spec.ts`).',
    '- **Rate limited** is timing-dependent (a burst races the leader state) and is covered by the reducer tests; not a stable frame.',
    '- **Suspended** and the **extension cap (10 of 10)** are reachable but slow to drive; left for a later pass.',
    '',
  ];
  writeFileSync(`${OUT}/index.md`, lines.join('\n'));
});
