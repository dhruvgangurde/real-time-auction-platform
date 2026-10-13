// brief 11.5: the landing route's JavaScript is at most 130 KB gzip. Runs after vite build:
// reads dist/.vite/manifest.json, prints every JS chunk's gzip size, sums the entry chunk with
// every chunk it imports statically, and exits 1 over the budget. No dependency: node:zlib.
import { readFileSync } from 'node:fs';
import { gzipSync } from 'node:zlib';

const BUDGET = 130 * 1024;
const dist = new URL('../dist/', import.meta.url);
const manifest = JSON.parse(readFileSync(new URL('.vite/manifest.json', dist), 'utf8'));

const gzip = (file) => gzipSync(readFileSync(new URL(file, dist)), { level: 9 }).length; // as vite reports
const kb = (n) => `${(n / 1024).toFixed(1)} KB`;

const chunks = Object.values(manifest).filter((c) => c.file.endsWith('.js'));
const sizes = new Map(chunks.map((c) => [c.file, gzip(c.file)]));
const entry = chunks.find((c) => c.isEntry);
if (entry === undefined) throw new Error('no entry chunk in the manifest');

// the entry total: the entry file plus everything it imports statically, transitively
const byFile = new Map(chunks.map((c) => [c.file, c]));
const seen = new Set();
const walk = (c) => {
  if (seen.has(c.file)) return;
  seen.add(c.file);
  for (const key of c.imports ?? []) walk(manifest[key]);
};
walk(entry);
const total = [...seen].reduce((n, f) => n + sizes.get(f), 0);

console.log('chunk'.padEnd(36) + 'gzip'.padStart(10) + '  part of entry');
for (const [file, size] of [...sizes].sort((a, b) => b[1] - a[1])) {
  console.log(file.replace('assets/', '').padEnd(36) + kb(size).padStart(10) + (seen.has(file) ? '  yes' : ''));
}
console.log(`entry total ${kb(total)} of ${kb(BUDGET)} (${seen.size} chunk${seen.size === 1 ? '' : 's'})`);
if (total > BUDGET) {
  console.error(`entry chunk over budget by ${kb(total - BUDGET)}`);
  process.exit(1);
}
void byFile;
