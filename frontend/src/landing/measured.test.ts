// Brief 8.4 and slice 5 Q1 (a): the numbers strip comes from the committed load-test results, each
// figure read from its requirement's own line by a strict pattern. A line in another format, or a
// requirement that did not pass, drops the strip: no figure is ever guessed, rounded or carried
// over. This test pins the figures of the committed file, so any drift fails here first.
import { expect, test } from 'vitest';

import { extract, latestResults, STRIP, type ResultsFile } from './measured';

test('the strip reads the newest committed results, exactly', () => {
  const file = latestResults();
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

const committed = () => structuredClone(latestResults()!) as ResultsFile;
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
