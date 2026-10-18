// Brief 8.4, slice 5 Q1 (a) and (b): the numbers strip comes from the committed load-test results:
// each requirement's figures block where the file has one (Phase 11 part 2), else its line read by
// a strict pattern (the file of 2026-10-17). A line in another format, a figures block that is not
// what the report writes, or a requirement that did not pass drops the strip: no figure is ever
// guessed, rounded or carried over. The committed file of 2026-10-17 is pinned by name.
import { expect, test } from 'vitest';

import { extract, latestResults, resultsFile, STRIP, type ResultsFile } from './measured';

test('the strip reads the file of 2026-10-17 from its sentences, exactly', () => {
  const file = resultsFile('2026-10-17');
  expect(file?.date).toBe('2026-10-17');
  const measured = extract(file!);
  expect(measured).not.toBeNull();
  expect(measured!.figures.map((f) => [f.id, f.value])).toEqual([
    ['NFR-1', '0.128 ms'],
    ['NFR-2', '10.0 ms'],
    ['NFR-3', '6.0 ms'],
    ['NFR-4', '37.6'],
    ['NFR-5', '110'],
    ['NFR-28', '0'],
  ]);
  expect(measured!.figures.find((f) => f.id === 'NFR-28')?.detail).toBe('17,356');
  expect(measured!.figures.find((f) => f.id === 'NFR-1')?.detail).toBe('27,691');
  expect(measured!.machine).toEqual({
    cpu: 'Intel(R) Core(TM) i9-14900HX',
    threads: 32,
    ramGib: 15.7,
    dockerCpus: 32,
    dockerGib: 7.6,
    k6: 'k6 v2.3.0',
  });
});

test('only measured values: NFR-8 and OR-6 are never in the strip', () => {
  expect(STRIP).toEqual(['NFR-1', 'NFR-2', 'NFR-3', 'NFR-4', 'NFR-5', 'NFR-28']);
  const ids = extract(latestResults()!)!.figures.map((f) => f.id);
  expect(ids).not.toContain('NFR-8');
  expect(ids).not.toContain('OR-6');
});

test('the newest committed results always make a strip', () => {
  expect(extract(latestResults()!)).not.toBeNull();
});

const committed = () => structuredClone(resultsFile('2026-10-17')!) as ResultsFile;
const withLine = (id: string, change: Partial<ResultsFile['requirements'][number]>): ResultsFile => {
  const file = committed();
  file.requirements = file.requirements.map((r) => (r.id === id ? { ...r, ...change } : r));
  return file;
};

test.each([
  ['NFR-1', 'p95 0.128ms; 27,691 EVALSHA entries for 17,356 accepted + 10,335 rejected bids'],
  ['NFR-1', 'not every bid: 27,690 EVALSHA entries for 17,356 accepted + 10,335 rejected bids'],
  ['NFR-2', 'p95 10 ms'],
  ['NFR-3', 'p95 about 6 ms'],
  ['NFR-4', 'slowest 5 s window 37.6 bids per second'],
  ['NFR-5', '110 sockets (api 55, api-2 55); 1 missing, 0 repeated deliveries'],
  ['NFR-28', '5/6 ledger checks passed on 17356 bids; concurrent-close tests 2/2 passed (OR-14)'],
])('a %s line in any other format drops the strip: %s', (id, measured) => {
  expect(extract(withLine(id, { measured }))).toBeNull();
});

test('a requirement that did not pass, or is missing, drops the strip', () => {
  expect(extract(withLine('NFR-4', { pass: false }))).toBeNull();
  expect(extract(withLine('NFR-2', { pass: null }))).toBeNull();
  const file = committed();
  file.requirements = file.requirements.filter((r) => r.id !== 'NFR-5');
  expect(extract(file)).toBeNull();
});

// a file with the figures block, as loadtest-report writes it since Phase 11 part 2; the sentences
// are made unreadable, so only the figures can give these values
const withFigures = (): ResultsFile => {
  const figures: Record<string, Record<string, unknown>> = {
    'NFR-1': { n: 30_001, p50_ms: 0.081, p95_ms: 0.131, p99_ms: 0.17, max_ms: 2.5, executions: 30_001, decided: 30_001 },
    'NFR-2': { n: 9000, p50_ms: 6.2, p95_ms: 11.04, p99_ms: 20, max_ms: 90 },
    'NFR-3': { n: 90_000, p50_ms: 2, p95_ms: 5.96, p99_ms: 9, max_ms: 80 },
    'NFR-4': { slowest_window_per_s: 36.2, median_window_per_s: 40, slowest_second: 30, window_s: 5, offered_per_s: 80, winnable_per_s: 41, accepted_run: 18_000 },
    'NFR-5': { sockets: 110, per_instance: { api: 55, 'api-2': 55 }, presence_peak: 110, missing: 0, duplicates: 0 },
    'NFR-28': { bids: 18_000, checks_passed: 6, checks: 6, concurrent_close_passed: true },
  };
  const file = committed();
  file.requirements = file.requirements.map((r) => (r.id in figures ? { ...r, measured: 'unreadable', figures: figures[r.id]! } : r));
  return file;
};

test('a file with figures is read from them, in the same form as the sentences', () => {
  const measured = extract(withFigures());
  expect(measured!.figures.map((f) => [f.id, f.value, f.detail])).toEqual([
    ['NFR-1', '0.131 ms', '30,001'],
    ['NFR-2', '11.0 ms', null],
    ['NFR-3', '6.0 ms', null],
    ['NFR-4', '36.2', null],
    ['NFR-5', '110', null],
    ['NFR-28', '0', '18,000'],
  ]);
});

test.each([
  ['NFR-1', { p95_ms: '0.131' }],
  ['NFR-1', { executions: 30_000 }], // fewer executions than bids decided: not every bid
  ['NFR-4', { slowest_window_per_s: null }],
  ['NFR-5', { missing: 1 }],
  ['NFR-28', { checks_passed: 5 }],
  ['NFR-28', { concurrent_close_passed: false }],
])('a %s figures block that is not what the report writes drops the strip: %j', (id, change) => {
  const file = withFigures();
  file.requirements = file.requirements.map((r) => (r.id === id ? { ...r, figures: { ...r.figures, ...change } } : r));
  expect(extract(file)).toBeNull();
});
