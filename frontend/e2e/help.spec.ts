// The help diagrams (UD-4, brief 9.11) on /help at 1440 and 390: every diagram renders, and no
// two of its labels intersect (the text boxes as the browser lays them out, fonts loaded).
import { expect, test } from '@playwright/test';

for (const width of [1440, 390]) {
  test(`no two labels of a help diagram intersect at ${width}`, async ({ browser }) => {
    const ctx = await browser.newContext({ viewport: { width, height: 900 } });
    const page = await ctx.newPage();
    await page.goto('/help');
    await expect(page.locator('svg[role="img"]')).toHaveCount(4);
    await page.evaluate(() => document.fonts.ready);
    const overlaps = await page.evaluate(() =>
      [...document.querySelectorAll('svg[role="img"]')].flatMap((svg) => {
        const boxes = [...svg.querySelectorAll('text')].map((t) => ({ text: t.textContent ?? '', r: t.getBoundingClientRect() }));
        const out: string[] = [];
        for (let i = 0; i < boxes.length; i += 1) {
          for (let j = i + 1; j < boxes.length; j += 1) {
            const a = boxes[i]!.r;
            const b = boxes[j]!.r;
            if (a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom) {
              out.push(`${svg.getAttribute('aria-label')?.slice(0, 24)}: "${boxes[i]!.text}" and "${boxes[j]!.text}"`);
            }
          }
        }
        return out;
      }),
    );
    expect(overlaps).toEqual([]);
    await ctx.close();
  });
}
