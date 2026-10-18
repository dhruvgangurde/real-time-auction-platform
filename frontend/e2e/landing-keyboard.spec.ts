// The landing from the keyboard (brief 8, 11.4; NFR-35): the hero's actions come before "Try to
// break it" in tab order, Send starts a run with Enter, the results' "How we checked" opens and
// closes with Enter and Space, and "Replay again" restarts the hero's replay; every stop shows the
// one focus ring. The demo endpoints are routed (demo mode is off on the e2e stack); the replayed
// lot is real.
import { expect, request, test, type Page } from '@playwright/test';

import { landingEnIN as l } from '../src/strings/landing-en-IN';
import { overview, replayLot, routeDemo, sequence, stressStatus, type ReplayLot } from './fixtures/landing';

let replay: ReplayLot;

test.beforeAll(async () => {
  test.setTimeout(120_000);
  const api = await request.newContext();
  replay = await replayLot(api, `Nikon ${Math.random().toString(36).slice(2, 8)} F3 with 50 mm f/1.4`);
  await api.dispose();
});

/** Tab until the focused element is named `name`; returns the names passed on the way. */
async function tabTo(page: Page, name: string, max = 60): Promise<string[]> {
  const passed: string[] = [];
  for (let i = 0; i < max; i += 1) {
    await page.keyboard.press('Tab');
    const focused = await page.evaluate(() => {
      const e = document.activeElement as HTMLElement | null;
      return (e?.getAttribute('aria-label') ?? e?.textContent ?? '').trim();
    });
    passed.push(focused);
    if (focused === name) return passed;
  }
  throw new Error(`never reached ${name}: ${passed.join(' | ')}`);
}

const ring = (page: Page) => page.evaluate(() => getComputedStyle(document.activeElement as Element).outlineStyle);

test('Send, the results and How we checked, from the keyboard', async ({ page }) => {
  test.setTimeout(90_000);
  const errors: string[] = [];
  page.on('console', (m) => m.type() === 'error' && errors.push(m.text()));
  page.on('pageerror', (e) => errors.push(e.message));
  let started = false;
  const statuses = sequence([stressStatus('running', 1), stressStatus('running', 2), stressStatus('settling', 3), stressStatus('verified', 4)]);
  await routeDemo(page, {
    // after the run: the cooldown, and the run's lot for the hero
    overview: () => (started ? overview(replay.id, { cooldown_until_ms: Date.now() + 60_000 }) : overview(null)),
    start: () => {
      started = true;
      return { status: 202, body: { run_id: 'run-e2e', state: 'requested', joined: false } };
    },
    status: statuses,
  });
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/');
  await expect(page.getByRole('button', { name: l.break.send })).toBeVisible();

  // tab order: the skip link, the header, the hero's actions, then Send
  const passed = await tabTo(page, l.break.send);
  const at = (name: string) => passed.indexOf(name);
  expect(at(l.hero.watch)).toBeGreaterThan(-1);
  expect(at(l.hero.watch)).toBeLessThan(at(l.hero.start));
  expect(at(l.hero.start)).toBeLessThan(at(l.break.send));
  expect(await ring(page)).toBe('solid');

  await page.keyboard.press('Enter');
  const polite = page.locator('section[aria-labelledby="break-title"] [aria-live="polite"]');
  await expect(polite).toHaveText(l.break.announce.running);
  await expect(polite).toHaveText(l.break.announce.verified, { timeout: 15_000 });
  await expect(page.getByText(l.break.postgres)).toBeVisible();
  await expect(page.getByText(l.break.pass, { exact: true })).toHaveCount(7); // six checks and the watcher
  await expect(page.getByRole('button', { name: /^Next run in / })).toBeDisabled();

  // How we checked: a native disclosure, Enter opens it and Space closes it
  await tabTo(page, l.break.how);
  expect(await ring(page)).toBe('solid');
  const how = page.locator('details');
  await page.keyboard.press('Enter');
  await expect(how).toHaveAttribute('open', '');
  await expect(how.locator('pre code').first()).toContainText('FROM bids');
  await page.keyboard.press('Space');
  await expect(how).not.toHaveAttribute('open');

  // the finished run reads GET /demo again: the hero now replays the run's lot
  await expect(page.getByRole('region', { name: l.board.label }).getByRole('heading', { name: replay.title })).toBeVisible();
  expect(errors).toEqual([]);
});

test('Replay again restarts the replay from the keyboard', async ({ page }) => {
  test.setTimeout(90_000);
  await routeDemo(page, { overview: () => overview(replay.id) });
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/');
  const board = page.getByRole('region', { name: l.board.label });
  await expect(board.getByRole('button', { name: l.board.again })).toBeVisible({ timeout: 45_000 });

  await tabTo(page, l.board.again);
  expect(await ring(page)).toBe('solid');
  await page.keyboard.press('Enter');
  await expect(board.getByRole('button', { name: l.board.again })).toHaveCount(0);
  await expect(board.getByText(l.board.startingPrice)).toBeVisible();
  // and it reaches the end again, where the button returns
  await expect(board.getByRole('button', { name: l.board.again })).toBeVisible({ timeout: 45_000 });
});
