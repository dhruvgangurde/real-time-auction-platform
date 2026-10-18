// Screenshots of the landing as built (brief 8; slice 5) for review, written to
// design/shots/08-landing with an index.md: every hero, "Try to break it" and section state at
// 1440 and 390, light and dark. Demo mode is off on the e2e stack, so the demo endpoints are routed
// (fixtures/landing.ts) for every state but "demo off"; the replayed lot, the featured lots and the
// figures are real. The real run's frames (real-*.png) come from e2e/landing-demo.spec.ts.
// PNGs are git-ignored (design/shots/**/*.png); this spec regenerates them.
import { mkdirSync, readdirSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { expect, request, test, type Locator, type Page } from '@playwright/test';

import { landingEnIN as l } from '../src/strings/landing-en-IN';
import { payEnIN as p } from '../src/strings/pay-en-IN';
import { overview, replayLot, routeDemo, stressStatus, type ReplayLot } from './fixtures/landing';
import { newAccount, newAuction, restBid, signInUi, waitForStatus, type Account } from './fixtures/stack';

const OUT = fileURLToPath(new URL('../../design/shots/08-landing', import.meta.url));
mkdirSync(OUT, { recursive: true });

type Json = Record<string, unknown>;
type Row = { state: string; file: string; note?: string };
const rows: Row[] = [];
const tag = () => Math.random().toString(36).slice(2, 8);

let replay: ReplayLot;
let buyer: Account;

test.beforeAll(async () => {
  test.setTimeout(180_000);
  const api = await request.newContext();
  const t = tag();
  replay = await replayLot(api, `Bidri ${t} betel box, octagonal`);
  // the featured lots: live with bids, live without, upcoming
  const seller = await newAccount(api, 'seller', 'seller');
  const bidder = await newAccount(api, 'bidder');
  buyer = await newAccount(api, 'buyer');
  await newAuction(api, seller, { title: `Mysore ${t} rosewood inlay panel`, startInS: 7200, endInS: 9000, reservePrice: null });
  await newAuction(api, seller, { title: `Tanjore ${t} glass painting`, startInS: 3600, endInS: 7200, reservePrice: null });
  const quiet = await newAuction(api, seller, { title: `Kutch ${t} mirror-work textile`, startInS: 2, endInS: 5400, reservePrice: null });
  const busy = await newAuction(api, seller, { title: `Channapatna ${t} lacquered toy set`, startInS: 2, endInS: 1800, reservePrice: null });
  await waitForStatus(api, quiet, 'live');
  await waitForStatus(api, busy, 'live');
  await (await restBid(api, bidder, busy, 900000)).json();
  await api.dispose();
});

/** What the routed demo endpoints answer now; each state sets it before loading the page. */
type Mode = {
  overview: Json | null;
  start?: { status: number; body: Json; headers?: Record<string, string> };
  /** what GET /demo answers once a run is started: the cooldown, as the server does */
  afterStart?: Json;
  status?: () => Json;
};
let mode: Mode = { overview: null };

const RUN_START = { status: 202, body: { run_id: 'run-e2e', state: 'requested', joined: false } };
const polls = (state: string, over: Json = {}) => {
  let version = 0;
  return () => stressStatus(state, ++version, over);
};

async function open(browser: import('@playwright/test').Browser, theme: 'light' | 'dark', reducedMotion: 'reduce' | 'no-preference' = 'no-preference') {
  const ctx = await browser.newContext({ colorScheme: theme, viewport: { width: 1440, height: 900 }, reducedMotion });
  const page = await ctx.newPage();
  await routeDemo(page, {
    overview: () => mode.overview,
    start: () => {
      if (mode.afterStart) mode.overview = mode.afterStart;
      return mode.start ?? { status: 404, body: { error: { code: 'NOT_FOUND', message: 'Not found.' } } };
    },
    status: () => (mode.status ? mode.status() : {}),
  });
  return { ctx, page };
}

const section = (page: Page, id: string) => page.locator(`section[aria-labelledby="${id}"]`);

/** 1440 then 390: the hero (headline to board), one section, or the full page. */
async function shoot(page: Page, state: string, theme: string, target: 'hero' | 'full' | Locator, note?: string) {
  for (const width of [1440, 390] as const) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 900 });
    await page.waitForTimeout(250);
    const file = `${state}-${width}-${theme}.png`;
    if (target === 'full') await page.screenshot({ path: `${OUT}/${file}`, fullPage: true });
    else if (target === 'hero') {
      await page.evaluate(() => window.scrollTo(0, 0));
      const below = await section(page, 'break-title').boundingBox();
      // scrolled to the top, the box's viewport y is its page y
      await page.screenshot({ path: `${OUT}/${file}`, fullPage: true, clip: { x: 0, y: 0, width, height: Math.ceil(below?.y ?? 900) } });
    } else await target.screenshot({ path: `${OUT}/${file}` });
    rows.push({ state, file, ...(note ? { note } : {}) });
  }
  await page.setViewportSize({ width: 1440, height: 900 });
}

for (const theme of ['light', 'dark'] as const) {
  test(`demo off, the sections and the full page (${theme})`, async ({ browser }) => {
    test.setTimeout(120_000);
    const { ctx, page } = await open(browser, theme);
    mode = { overview: null };
    await page.goto('/');
    await expect(page.getByText(l.break.unavailable.off)).toBeVisible();
    await expect(section(page, 'lots-title').locator('figure').first()).toBeVisible();
    await page.evaluate(() => document.fonts.ready);
    await shoot(page, 'hero-01-empty-demo-off', theme, 'hero');
    await shoot(page, 'break-14-unavailable-off', theme, section(page, 'break-title'));
    await shoot(page, 'section-01-measured', theme, section(page, 'measured-title'));
    await shoot(page, 'section-02-featured', theme, section(page, 'lots-title'));
    await shoot(page, 'section-04-built', theme, section(page, 'built-title'));
    await shoot(page, 'section-05-footer', theme, page.locator('main footer'));
    await shoot(page, 'page-01-full-demo-off', theme, 'full');

    // no live or upcoming lots: the list answer emptied in the browser
    await page.route(/\/api\/v1\/auctions\?/, async (route) => {
      const real = (await (await route.fetch()).json()) as Json;
      await route.fulfill({ json: { ...real, items: [], has_more: false } });
    });
    await page.goto('/');
    await expect(page.getByText(l.featured.none)).toBeVisible();
    await shoot(page, 'section-03-featured-empty', theme, section(page, 'lots-title'), 'GET /auctions routed to no items');
    await ctx.close();
  });

  test(`the hero's replay, from loading to closed (${theme})`, async ({ browser }) => {
    test.setTimeout(120_000);
    const { ctx, page } = await open(browser, theme);
    mode = { overview: overview(null) };
    await page.goto('/');
    await expect(page.getByRole('heading', { name: l.board.emptyTitle })).toBeVisible();
    await shoot(page, 'hero-02-empty-no-run', theme, 'hero', 'demo on, no verified run yet');

    // the ledger held back for a few seconds: the board waits for it
    let hold = true;
    await page.route(/\/api\/v1\/auctions\/[0-9a-f-]{36}\/bids\?/, async (route) => {
      while (hold) await new Promise((r) => setTimeout(r, 200));
      await route.fallback();
    });
    mode = { overview: overview(replay.id) };
    await page.goto('/');
    const board = page.getByRole('region', { name: l.board.label });
    await expect(board.getByText(l.board.loading)).toBeVisible();
    await shoot(page, 'hero-03-replay-loading', theme, 'hero', 'the ledger request held back');
    hold = false;
    await expect(board.getByText(l.board.startingPrice)).toBeVisible();
    await shoot(page, 'hero-04-replay-early', theme, 'hero', 'before the first bid');
    await expect(board.locator('tbody tr')).toHaveCount(4, { timeout: 20_000 });
    await shoot(page, 'hero-05-replay-mid', theme, 'hero', 'every bid in, the clock still running');
    await expect(board.getByRole('button', { name: l.board.again })).toBeVisible({ timeout: 40_000 });
    await shoot(page, 'hero-06-replay-closed', theme, 'hero');
    await ctx.close();

    const reduced = await open(browser, theme, 'reduce');
    await reduced.page.goto('/');
    const still = reduced.page.getByRole('region', { name: l.board.label });
    await expect(still.locator('tbody tr')).toHaveCount(4, { timeout: 25_000 });
    await shoot(reduced.page, 'hero-07-reduced-motion', theme, 'hero', 'prefers-reduced-motion: no band, no digit tick, no row entrance');
    await reduced.ctx.close();
  });

  test(`Try to break it, every state (${theme})`, async ({ browser }) => {
    test.setTimeout(180_000);
    const { ctx, page } = await open(browser, theme);
    const box = section(page, 'break-title');
    const at = async (m: Mode, send: boolean, wait: string | RegExp) => {
      mode = m;
      await page.goto('/');
      if (send) await page.getByRole('button', { name: l.break.send }).click();
      await expect(box.getByText(wait).first()).toBeVisible({ timeout: 15_000 });
    };
    const ready = overview(null);
    const after = overview(null, { cooldown_until_ms: Date.now() + 600_000 });
    const sent = (status: () => Json): Mode => ({ overview: ready, start: RUN_START, afterStart: after, status });

    await at({ overview: ready }, false, l.break.willCheck);
    await shoot(page, 'break-01-ready', theme, box);
    await at({ overview: overview(null, { active_run_id: 'run-e2e' }), status: polls('running') }, false, l.break.joined);
    await shoot(page, 'break-02-already-running', theme, box, 'GET /demo names an active run');
    await at(sent(polls('running')), true, l.break.watchingRow);
    await shoot(page, 'break-03-running', theme, box);
    await at(sent(polls('settling')), true, l.break.settling);
    await shoot(page, 'break-04-settling', theme, box);
    await at(sent(polls('verified')), true, l.break.postgres);
    await shoot(page, 'break-05-verified', theme, box);
    await box.getByText(l.break.how).click();
    await shoot(page, 'break-06-verified-how-we-checked', theme, box);
    await at(sent(polls('failed')), true, l.break.failMeaning.gapless);
    await shoot(page, 'break-07-check-failed', theme, box, 'check 1 failed: number 57 missing');
    await at(sent(polls('verified', { watcher: { auction_closed_received: 0, expected: 1 } })), true, l.break.watcherFail);
    await shoot(page, 'break-08-watcher-failed', theme, box, 'the Postgres checks passed; the watcher received 0');
    await at(sent(polls('aborted')), true, l.break.stopped);
    await shoot(page, 'break-09-stopped', theme, box);
    await at({ overview: overview(null, { cooldown_until_ms: Date.now() + 42_000 }) }, false, /^Next run in /);
    await shoot(page, 'break-10-cooling', theme, box);
    for (const [n, reason, text] of [
      ['11', 'disabled', l.break.unavailable.disabled],
      ['12', 'runner_offline', l.break.unavailable.runner_offline],
      ['13', 'daily_cap', l.break.unavailable.daily_cap],
    ] as const) {
      await at({ overview: overview(null, { unavailable_reason: reason }) }, false, text);
      await shoot(page, `break-${n}-unavailable-${reason.replace('_', '-')}`, theme, box);
    }
    const limited = { status: 429, body: { error: { code: 'RATE_LIMITED', message: 'Too many requests.' } }, headers: { 'retry-after': '1800' } };
    await at({ overview: ready, start: limited }, true, /^This address has started its runs for this hour/);
    await shoot(page, 'break-15-unavailable-rate-limited', theme, box, 'POST /demo/stress routed to 429 with Retry-After 1800');
    await ctx.close();
  });

  test(`the payment page for a demo lot (${theme})`, async ({ browser }) => {
    const { ctx, page } = await open(browser, theme);
    await signInUi(page, buyer);
    await page.route(`**/api/v1/auctions/${replay.id}/settlement`, (route) =>
      route.fulfill({
        json: {
          id: '00000000-0000-4000-8000-00000000d3e1',
          auction_id: replay.id,
          status: 'demo',
          final_price: 1050000,
          winner: { id: buyer.id, full_name: buyer.name },
          created_at_ms: Date.now() - 60_000,
          paid_at_ms: null,
          payment: null,
        },
      }),
    );
    await page.goto(`/auctions/${replay.id}/pay`);
    await expect(page.getByText(p.demo)).toBeVisible();
    await shoot(page, 'pay-01-demo-lot', theme, 'full', 'the settlement routed to status demo (no demo lot can exist with demo mode off)');
    await ctx.close();
  });
}

test.afterAll(() => {
  rows.sort((a, b) => a.state.localeCompare(b.state) || a.file.localeCompare(b.file));
  const esc = (s: string) => s.replace(/\|/g, '\\|').trim();
  const real = readdirSync(OUT)
    .filter((f) => f.startsWith('real-') && f.endsWith('.png'))
    .sort();
  const lines = [
    '# 08-landing shots',
    '',
    'The landing at `/` as built (`frontend/src/app/Home.tsx`, `frontend/src/landing/`), rendered on 8011 by `frontend/e2e/landing-shots.spec.ts`. Demo mode is off on the e2e stack, so the demo endpoints (GET /demo, POST /demo/stress, GET /demo/stress/{id}) are routed in the browser for every state but "demo off", with bodies in the shapes of `backend/app/services/demo.py` and `demo_checks.py`. The replayed lot and its ledger, the featured lots and the Measured figures are real. PNGs are git-ignored (`design/shots/**/*.png`); re-make them with `cd frontend && npm run e2e -- e2e/landing-shots.spec.ts`.',
    '',
    'Every state at 1440 and 390, light and dark. Hero frames run from the top of the page to the end of the board; section frames are the section alone; `page-` frames are the full page.',
    '',
    '| State | File |',
    '|---|---|',
    ...rows.map((r) => `| ${r.state}${r.note ? ` (${esc(r.note)})` : ''} | ${r.file} |`),
    '',
    '## A real run',
    '',
    real.length > 0
      ? 'From `frontend/e2e/landing-demo.spec.ts` (E2E_DEMO=1) on the dev stack with demo mode on and the runner: one real stress run, nothing routed.'
      : 'Not yet made: run `E2E_DEMO=1 npm run e2e -- e2e/landing-demo.spec.ts` against the dev stack with `DEMO_MODE=true docker compose --profile demo up -d`.',
    '',
    ...real.map((f) => `- ${f}`),
    '',
  ];
  writeFileSync(`${OUT}/index.md`, lines.join('\n'));
});
