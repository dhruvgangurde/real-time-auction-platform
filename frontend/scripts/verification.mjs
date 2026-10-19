// OR-12's verification matrix: every SRS requirement ID mapped to its verification activities,
// written to docs/verification.md. From the repository:
//   npm run verification          writes docs/verification.md
//   npm run verification:check    fails if the file is stale, or a cited ID is not in the SRS
// Sources, all in the repository: verification/srs-ids.tsv (the SRS's 199 IDs), the tests that
// cite them (backend/tests test_*.py: module docstring or a test's docstring; frontend/src *.test.ts and
// frontend/e2e *.spec.ts: the header comment or a test's title), the newest load-test results
// (loadtest/results/*.json), and verification/manual.tsv (inspection, demonstration, analysis).
// An ID with none of them is listed as a gap, never left out.
import { readdirSync, readFileSync, statSync, writeFileSync } from 'node:fs';
import { join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = fileURLToPath(new URL('../..', import.meta.url));
const OUT = join(ROOT, 'docs', 'verification.md');
const PREFIXES = 'OE|DC|UD|AS|DP|UI|HI|SI|CI|FR|NFR|BR|OR';
const ID = new RegExp(`\\b(${PREFIXES})-(\\d+)\\b`, 'g');
const RANGE = new RegExp(`\\b(${PREFIXES})-(\\d+) to (?:(${PREFIXES})-)?(\\d+)\\b`, 'g');
const SHOWN = 6; // locations per row before "and N more"

const rel = (path) => relative(ROOT, path).replaceAll('\\', '/');
// a Windows checkout may have CRLF line ends: every pattern below reads LF
const read = (path) => readFileSync(path, 'utf8').replaceAll('\r\n', '\n');
const tsv = (path) =>
  read(join(ROOT, path))
    .split('\n')
    .filter((line) => line.trim() !== '' && !line.startsWith('#'))
    .map((line) => line.split('\t'));

/** Every requirement ID in a text: single IDs, and both ends and the inside of "X-a to X-b". */
export function citedIds(text) {
  const found = new Set();
  for (const [, prefix, from, again, to] of text.matchAll(RANGE)) {
    if (again !== undefined && again !== prefix) continue;
    for (let n = Number(from); n <= Number(to) && n - Number(from) < 100; n += 1) found.add(`${prefix}-${n}`);
  }
  for (const [, prefix, n] of text.matchAll(ID)) found.add(`${prefix}-${Number(n)}`);
  return found;
}

function walk(dir, keep) {
  const out = [];
  for (const name of readdirSync(dir)) {
    if (name === 'node_modules' || name === '__pycache__' || name.startsWith('.')) continue;
    const path = join(dir, name);
    if (statSync(path).isDirectory()) out.push(...walk(path, keep));
    else if (keep(name)) out.push(path);
  }
  return out.sort();
}

/** backend/tests: the module docstring (the file) and each test's docstring (that test). */
function pythonCitations(path) {
  const text = read(path);
  const out = [];
  const module = /^\s*(?:"""|''')([\s\S]*?)(?:"""|''')/.exec(text);
  if (module) for (const id of citedIds(module[1])) out.push({ id, where: rel(path) });
  // the signature may span lines, but never into the next definition
  const test = /^[ \t]*(?:async )?def (test_\w+)\((?:(?!\n[ \t]*(?:async )?def |\n[ \t]*@)[^])*?\)[^:\n]*:[ \t]*\n\s*(?:"""|''')([\s\S]*?)(?:"""|''')/gm;
  for (const [, name, doc] of text.matchAll(test)) for (const id of citedIds(doc)) out.push({ id, where: `${rel(path)}::${name}` });
  return out;
}

/** frontend tests and specs: the header comment (the file) and each test's title (that test). */
function tsCitations(path) {
  const text = read(path);
  const out = [];
  const header = text.split('\n').filter((_, i, lines) => lines.slice(0, i + 1).every((l) => l.startsWith('//')));
  for (const id of citedIds(header.join('\n'))) out.push({ id, where: rel(path) });
  const title = /\btest(?:\.each\([^)]*\))?\(\s*(['"`])((?:(?!\1)[^\\]|\\.)*)\1/g;
  for (const [, , name] of text.matchAll(title)) for (const id of citedIds(name)) out.push({ id, where: `${rel(path)}: ${name}` });
  return out;
}

function loadTestCitations() {
  const dir = join(ROOT, 'loadtest', 'results');
  const newest = readdirSync(dir).filter((f) => f.endsWith('.json')).sort().at(-1);
  if (newest === undefined) return [];
  const file = JSON.parse(readFileSync(join(dir, newest), 'utf8'));
  const verdict = { true: 'pass', false: 'not met', null: 'not assessed' };
  return file.requirements.map((r) => ({ id: r.id, where: `loadtest/RESULTS.md, ${r.id} (${newest.replace('.json', '')}: ${verdict[String(r.pass)]})` }));
}

export function build() {
  const srs = tsv('verification/srs-ids.tsv').map(([id, title]) => ({ id, title }));
  const known = new Set(srs.map((r) => r.id));
  const activities = new Map(srs.map((r) => [r.id, []]));
  const unknown = [];
  const add = (kind, { id, where }) => {
    if (!known.has(id)) unknown.push(`${id} in ${where}`);
    else activities.get(id).push({ kind, where });
  };
  // test modules only: helpers, fakes and conftest.py hold no tests, so they verify nothing (audit M12)
  for (const path of walk(join(ROOT, 'backend', 'tests'), (n) => /^test_\w+\.py$/.test(n))) for (const c of pythonCitations(path)) add('test', c);
  for (const path of walk(join(ROOT, 'frontend', 'src'), (n) => /\.test\.tsx?$/.test(n))) for (const c of tsCitations(path)) add('test', c);
  for (const path of walk(join(ROOT, 'frontend', 'e2e'), (n) => n.endsWith('.spec.ts'))) for (const c of tsCitations(path)) add('test', c);
  for (const c of loadTestCitations()) add('test', c);
  for (const [id, kind, where] of tsv('verification/manual.tsv')) {
    if (!['inspection', 'demonstration', 'analysis'].includes(kind)) unknown.push(`${id}: unknown kind "${kind}" in verification/manual.tsv`);
    add(kind, { id, where });
  }

  // why each gap is one (verification/gaps.tsv): a reason only, it never maps an ID
  const why = new Map(tsv('verification/gaps.tsv').map(([id, reason]) => [id, reason]));
  for (const id of why.keys()) if (!known.has(id)) unknown.push(`${id} in verification/gaps.tsv`);
  const cell = (s) => s.replaceAll('|', '\\|');
  const rows = [];
  const gaps = [];
  for (const { id, title } of srs) {
    const found = activities.get(id);
    if (found.length === 0) {
      gaps.push({ id, title });
      continue;
    }
    const kinds = [...new Set(found.map((a) => a.kind))].sort().join(', ');
    const places = [...new Set(found.map((a) => (a.kind === 'test' ? a.where : `${a.kind}: ${a.where}`)))].sort();
    const shown = places.slice(0, SHOWN).map((p) => `\`${cell(p)}\``);
    if (places.length > SHOWN) shown.push(`and ${places.length - SHOWN} more`);
    rows.push(`| ${id} | ${cell(title)} | ${kinds} | ${shown.join('<br>')} |`);
  }
  const mapped = srs.length - gaps.length;
  const page = [
    `<!-- verification ids=${srs.length} mapped=${mapped} gaps=${gaps.length} -->`,
    '# Verification matrix',
    '',
    'OR-12: "Every requirement in this document shall be mapped to at least one verification activity -- inspection, demonstration, test or analysis -- in the project test plan." Every requirement ID of the SRS is here: with the activities that verify it, or in Gaps.',
    '',
    `**${mapped} of ${srs.length} requirements mapped; ${gaps.length} without a verification activity, listed in [Gaps](#gaps).**`,
    '',
    'Generated by `frontend/scripts/verification.mjs` (`npm run verification`); `npm run verification:check` (also run by `npm test`) fails when this page is stale or a test cites an ID the SRS does not have. A test counts when it cites the ID in its module docstring or header comment (the whole file) or in its own docstring or title (that test); only test modules count (backend `test_*.py`, frontend `*.test.ts` and `*.spec.ts`), never helpers, fakes or `conftest.py`; a range like FR-52 to FR-56 cites each ID in it. Load-test rows come from the newest `loadtest/results/*.json`. Inspections, demonstrations and analyses are listed in `verification/manual.tsv`, each with where to look.',
    '',
    '## Matrix',
    '',
    '| ID | Requirement | Activity | Where |',
    '|---|---|---|---|',
    ...rows,
    '',
    `## Gaps`,
    '',
    gaps.length === 0 ? 'None.' : `${gaps.length} requirements have no verification activity yet:`,
    '',
    ...(gaps.length === 0
      ? []
      : ['| ID | Requirement | Why it is a gap |', '|---|---|---|', ...gaps.map((g) => `| ${g.id} | ${cell(g.title)} | ${cell(why.get(g.id) ?? 'not yet recorded')} |`), '']),
    '## Open items',
    '',
    'TBD-1 (the transactional email provider) and TBD-2 (demo hosting and TLS termination) are open decisions in the SRS, not requirements.',
    '',
  ].join('\n');
  return { page, unknown, mapped, gaps };
}

const check = process.argv.includes('--check');
if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  const { page, unknown, mapped, gaps } = build();
  if (unknown.length > 0) {
    console.error(`cited but not in the SRS (verification/srs-ids.tsv):\n  ${unknown.join('\n  ')}`);
    process.exit(1);
  }
  if (check) {
    let current = '';
    try {
      current = read(OUT);
    } catch {
      /* missing: stale */
    }
    if (current !== page) {
      console.error('docs/verification.md is stale: run npm run verification');
      process.exit(1);
    }
    console.log(`docs/verification.md is current: ${mapped} mapped, ${gaps.length} gaps`);
  } else {
    writeFileSync(OUT, page);
    console.log(`docs/verification.md written: ${mapped} mapped, ${gaps.length} gaps`);
  }
}
