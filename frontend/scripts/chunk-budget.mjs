// brief 11.5: each document's JavaScript on first load is at most 130 KB gzip: the SPA's entry
// (index.html) and the payment page's (pay.html, brief 13 item 25), each its own build. Runs after
// both vite builds: reads their two manifests, prints every JS chunk's gzip size, sums each HTML
// entry with every chunk it imports statically, and exits 1 over the budget. No dependency.
import { readFileSync } from 'node:fs';
import { gzipSync } from 'node:zlib';

const BUDGET = 130 * 1024;
const dist = new URL('../dist/', import.meta.url);
// two builds, one manifest each: the SPA's and the payment document's (vite.pay.config.ts)
const manifests = ['.vite/manifest.json', '.vite/pay-manifest.json'].map((f) => JSON.parse(readFileSync(new URL(f, dist), 'utf8')));

const gzip = (file) => gzipSync(readFileSync(new URL(file, dist)), { level: 9 }).length; // as vite reports
const kb = (n) => `${(n / 1024).toFixed(1)} KB`;

const chunks = manifests.flatMap((m) => Object.values(m)).filter((c) => c.file.endsWith('.js'));
const sizes = new Map(chunks.map((c) => [c.file, gzip(c.file)]));

/** The entry file plus everything it imports statically, transitively, within its manifest. */
function closure(manifest, entry) {
  const seen = new Set();
  const walk = (c) => {
    if (seen.has(c.file)) return;
    seen.add(c.file);
    for (const key of c.imports ?? []) walk(manifest[key]);
  };
  walk(entry);
  return seen;
}

const totals = manifests.flatMap((m) =>
  Object.values(m)
    .filter((c) => c.isEntry)
    .map((entry) => {
      const files = closure(m, entry);
      return { src: entry.src, files, total: [...files].reduce((n, f) => n + sizes.get(f), 0) };
    }),
);
if (totals.length !== 2) throw new Error(`expected the SPA and the payment entries, found ${totals.length}`);

console.log('chunk'.padEnd(36) + 'gzip'.padStart(10) + '  first load of');
for (const [file, size] of [...sizes].sort((a, b) => b[1] - a[1])) {
  const of = totals.filter((t) => t.files.has(file)).map((t) => t.src);
  console.log(file.replace('assets/', '').padEnd(36) + kb(size).padStart(10) + (of.length ? `  ${of.join(', ')}` : ''));
}
let over = false;
for (const t of totals) {
  console.log(`entry total ${kb(t.total)} of ${kb(BUDGET)} (${t.src}, ${t.files.size} chunk${t.files.size === 1 ? '' : 's'})`);
  if (t.total > BUDGET) {
    console.error(`${t.src}: over budget by ${kb(t.total - BUDGET)}`);
    over = true;
  }
}
if (over) process.exit(1);
