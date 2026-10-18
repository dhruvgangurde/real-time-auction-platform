// The colour theme control in the header (System, Light, Dark) on 8011 with the real CSP, at 1440
// and 390: keyboard operable and labelled, System follows the device, a choice sets data-theme
// and the paper colour at once, survives a reload (public/theme.js applies it in <head> before
// first paint: the control itself never writes on mount), and the payment page honours it.
import { expect, test, type Page } from '@playwright/test';

import { enIN } from '../src/strings/en-IN';

const PAPER = { light: 'rgb(255, 255, 255)', dark: 'rgb(13, 20, 36)' } as const;
const n = enIN.nav;
const label = (current: keyof typeof n.themes, next: keyof typeof n.themes) => n.themeLabel(n.themes[current], n.themes[next]);

declare global {
  interface Window {
    cspViolations: string[];
  }
}

const paper = (page: Page) => page.evaluate(() => getComputedStyle(document.body).backgroundColor);
const chosen = (page: Page) => page.evaluate(() => document.documentElement.dataset.theme ?? null);

for (const viewport of [
  { width: 1440, height: 900 },
  { width: 390, height: 844 },
]) {
  test(`the theme control at ${viewport.width}: System, Light, Dark, kept across a reload and on the payment page`, async ({ browser }) => {
    const context = await browser.newContext({ viewport, colorScheme: 'dark' }); // the device is dark
    const page = await context.newPage();
    await page.addInitScript(() => {
      window.cspViolations = [];
      document.addEventListener('securitypolicyviolation', (e) => window.cspViolations.push(`${e.violatedDirective} ${e.blockedURI}`));
    });
    await page.goto('/');
    const control = page.getByRole('button', { name: label('system', 'light') });
    await expect(control).toBeVisible();
    expect(await chosen(page)).toBeNull();
    expect(await paper(page)).toBe(PAPER.dark); // System follows the device

    await control.focus();
    await page.keyboard.press('Enter');
    await expect(page.getByRole('button', { name: label('light', 'dark') })).toBeFocused();
    expect(await chosen(page)).toBe('light');
    expect(await paper(page)).toBe(PAPER.light); // Light although the device is dark

    await page.keyboard.press('Space');
    await expect(page.getByRole('button', { name: label('dark', 'system') })).toBeVisible();
    expect(await chosen(page)).toBe('dark');

    await page.reload();
    expect(await chosen(page)).toBe('dark'); // set by /theme.js in <head>
    expect(await paper(page)).toBe(PAPER.dark);
    await expect(page.getByRole('button', { name: label('dark', 'system') })).toBeVisible();

    await page.goto('/auctions/00000000-0000-0000-0000-000000000000/pay'); // its own document and CSP
    expect(await chosen(page)).toBe('dark');
    expect(await paper(page)).toBe(PAPER.dark);

    await page.goto('/');
    await page.getByRole('button', { name: label('dark', 'system') }).click();
    expect(await chosen(page)).toBeNull();
    expect(await page.evaluate(() => localStorage.getItem('theme'))).toBeNull();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    expect(await page.evaluate(() => window.cspViolations)).toEqual([]);
    await context.close();
  });
}
