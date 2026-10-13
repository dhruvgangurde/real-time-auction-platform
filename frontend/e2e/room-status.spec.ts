// The connection dot (brief 9.3, 6): green only when the room is genuinely live; degraded or
// stale shows a hollow dot and the word carries the state.
import { execFileSync } from 'node:child_process';

import { expect, test } from '@playwright/test';

import { enIN } from '../src/strings/en-IN';
import { ROOT, newAccount, newAuction, restBid, signInUi, waitForStatus } from './fixtures/stack';

const GREEN = ['rgb(15, 107, 75)', 'rgb(92, 211, 162)']; // light, dark --green

async function greenCount(page: import('@playwright/test').Page): Promise<number> {
  return page.evaluate(
    (colors) => [...document.querySelectorAll('*')].filter((el) => colors.includes(getComputedStyle(el).backgroundColor)).length,
    GREEN,
  );
}

test('the dot is green when live and hollow when degraded', async ({ browser, request }) => {
  test.setTimeout(140_000);
  const seller = await newAccount(request, 'seller', 'seller');
  const alice = await newAccount(request, 'alice');
  const bob = await newAccount(request, 'bob');
  const id = await newAuction(request, seller, { endInS: 600, reservePrice: null });
  await waitForStatus(request, id, 'live');
  await (await restBid(request, bob, id, 900000)).json();

  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  await signInUi(page, alice);
  await page.goto(`/auctions/${id}`);
  await expect(page.getByText(/Connected,/)).toBeVisible({ timeout: 10_000 });
  expect(await greenCount(page), 'the live dot is green').toBeGreaterThan(0);

  // Redis down: the room is degraded, so the dot must not be the connected green
  execFileSync('docker', ['compose', 'pause', 'redis'], { cwd: ROOT, stdio: 'pipe' });
  try {
    await expect(page.getByText(enIN.roomUi.banner.degraded).first()).toBeVisible({ timeout: 20_000 });
    expect(await greenCount(page), 'no green dot while degraded').toBe(0);
  } finally {
    execFileSync('docker', ['compose', 'unpause', 'redis'], { cwd: ROOT, stdio: 'pipe' });
  }
  await ctx.close();
});
