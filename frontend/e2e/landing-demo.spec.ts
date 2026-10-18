// "Try to break it" for real (brief 8.1, 8.3; slice 5 amendments 1 and 2): one stress run through
// Ready, Running, Settling, Verified and Cooling down, then the hero replays that run. The page's
// requests stay under the anonymous per-address limit throughout: zero 429 responses. Gated: it
// needs the dev stack at production limits with demo mode on and the runner, not the e2e stack:
//   DEMO_MODE=true docker compose --profile demo up -d
//   E2E_DEMO=1 npm run e2e -- e2e/landing-demo.spec.ts
// Real-state frames go to design/shots/08-landing/real-*.png (git-ignored).
import { mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { expect, test, type Page } from '@playwright/test';

import { landingEnIN as l } from '../src/strings/landing-en-IN';

const OUT = fileURLToPath(new URL('../../design/shots/08-landing', import.meta.url));

test.skip(!process.env.E2E_DEMO, 'needs E2E_DEMO=1 and the dev stack with DEMO_MODE=true --profile demo');

const shot = async (page: Page, name: string) => {
  mkdirSync(OUT, { recursive: true });
  await page.screenshot({ path: `${OUT}/real-${name}-1440-light.png`, fullPage: true });
};

test('a real run: Ready, Running, Settling, Verified, Cooling down, then the replay on the hero', async ({ page }) => {
  test.setTimeout(420_000);
  const limited: string[] = [];
  const sockets: string[] = [];
  const errors: string[] = [];
  const listed: string[] = [];
  let runLot: string | null = null;
  page.on('websocket', (ws) => sockets.push(ws.url()));
  page.on('console', (m) => m.type() === 'error' && errors.push(m.text()));
  page.on('pageerror', (e) => errors.push(e.message));
  page.on('response', async (r) => {
    const url = new URL(r.url());
    if (r.status() === 429) limited.push(`${r.request().method()} ${url.pathname}`);
    if (!url.pathname.startsWith('/api/v1/') || r.status() !== 200) return;
    try {
      if (url.pathname.startsWith('/api/v1/demo/stress/')) runLot = ((await r.json()) as { auction_id: string | null }).auction_id ?? runLot;
      if (url.pathname === '/api/v1/auctions') listed.push(...((await r.json()) as { items: { id: string }[] }).items.map((i) => i.id));
    } catch {
      /* a body that cannot be read */
    }
  });
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/');

  // Ready (after any cooldown left by an earlier run)
  const send = page.getByRole('button', { name: l.break.send });
  await expect(send).toBeVisible({ timeout: 90_000 });
  await expect(page.getByText(l.break.willCheck)).toBeVisible();
  await page.locator('#break-title').scrollIntoViewIfNeeded();
  await shot(page, '01-ready');

  await send.click();
  const polite = page.locator('section[aria-labelledby="break-title"] [aria-live="polite"]');
  await expect(polite).toHaveText(l.break.announce.running, { timeout: 30_000 });
  await expect(page.getByRole('button', { name: /^Running, / })).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText(l.break.counters.of(50, 50))).toBeVisible({ timeout: 30_000 });
  await page.waitForTimeout(15_000); // well into the run: bids in the counters and the recent rows
  await shot(page, '02-running');

  await expect(polite).toHaveText(l.break.announce.settling, { timeout: 120_000 });
  await shot(page, '03-settling');

  await expect(polite).toHaveText(l.break.announce.verified, { timeout: 180_000 });
  await expect(page.getByText(l.break.postgres)).toBeVisible();
  const checks = page.locator('section[aria-labelledby="break-title"]').getByText(l.break.pass, { exact: true });
  await expect(checks).toHaveCount(7); // six Postgres checks and the watcher
  await shot(page, '04-verified');

  // Cooling down: the button counts down to the next run
  await expect(page.getByRole('button', { name: /^Next run in / })).toBeDisabled();
  await shot(page, '05-cooling');

  // the hero now replays this run, from its ledger, and plays to the end
  expect(runLot).not.toBeNull();
  const board = page.getByRole('region', { name: l.board.label });
  await board.scrollIntoViewIfNeeded();
  await expect(board.getByText(/^Replay of a real run, /)).toBeVisible({ timeout: 15_000 });
  await expect(board.locator('tbody tr')).not.toHaveCount(0, { timeout: 30_000 });
  await page.waitForTimeout(20_000);
  await shot(page, '06-replay-mid');
  await expect(board.getByRole('button', { name: l.board.again })).toBeVisible({ timeout: 150_000 });
  await shot(page, '07-replay-closed');
  await board.getByRole('button', { name: l.board.again }).click();
  await expect(board.getByText(l.board.startingPrice)).toBeVisible();

  // the run's lot is a demo lot: never among the featured lots, nor in any list the page read
  await page.reload();
  await expect(page.getByRole('region', { name: l.featured.title })).toBeVisible();
  await page.waitForTimeout(3_000);
  expect(listed).not.toContain(runLot);
  await expect(page.getByRole('region', { name: l.featured.title }).locator(`a[href="/auctions/${runLot}"]`)).toHaveCount(0);

  console.log(`429 responses during the run and the replay: ${limited.length}${limited.length ? ` (${limited.join(', ')})` : ''}`);
  expect(limited).toEqual([]);
  expect(sockets).toEqual([]);
  expect(errors).toEqual([]);
});
