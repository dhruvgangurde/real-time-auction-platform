// Brief 8.4, the numbers strip: measured results from the committed load-test results file
// (loadtest/results/<date>.json, written by `bash loadtest/run.sh`), read at build time. Only
// measured values, and only the requirements the brief lists. The results file holds each figure
// as its requirement's line of text, so each is read by a strict pattern (slice 5 Q1 (a)): a line in
// any other format, or a requirement that did not pass, drops the whole strip rather than show a
// figure that might be wrong (measured.test.ts pins the committed figures). A structured figures
// block in the results file is for Phase 11 part 2.
const FILES = import.meta.glob<ResultsFile>('../../../loadtest/results/*.json', { eager: true, import: 'default' });

export type ResultsFile = {
  date: string;
  requirements: { id: string; target: string; measured: string; pass: boolean | null }[];
  machine: {
    cpu: string;
    threads: number;
    ram_gib: number;
    docker: { cpus: number; memory_gib: number };
    k6: string;
  };
};

export type Figure = { id: string; value: string; detail: string | null };
export type Machine = { cpu: string; threads: number; ramGib: number; dockerCpus: number; dockerGib: number; k6: string };
export type Measured = { date: string; figures: Figure[]; machine: Machine };

/** The brief's six (8.4): arbitration, submission to broadcast, delivery, accepted bids per second,
 * connections in one room, lost or duplicated bids. NFR-8 and OR-6 are not measured values. */
export const STRIP = ['NFR-1', 'NFR-2', 'NFR-3', 'NFR-4', 'NFR-5', 'NFR-28'] as const;

const grouped = new Intl.NumberFormat('en-IN');

// the exact lines loadtest-report writes (backend app/loadtest.py requirements())
const PATTERNS: Record<(typeof STRIP)[number], { line: RegExp; read: (m: RegExpExecArray) => [string, string | null] }> = {
  'NFR-1': {
    line: /^p95 (\d+\.\d{3}) ms; ([\d,]+) EVALSHA entries for [\d,]+ accepted \+ [\d,]+ rejected bids$/,
    read: (m) => [`${m[1]} ms`, m[2]!],
  },
  'NFR-2': { line: /^p95 (\d+\.\d) ms$/, read: (m) => [`${m[1]} ms`, null] },
  'NFR-3': { line: /^p95 (\d+\.\d) ms$/, read: (m) => [`${m[1]} ms`, null] },
  'NFR-4': { line: /^slowest 5 s window (\d+\.\d) bids\/s$/, read: (m) => [m[1]!, null] },
  'NFR-5': { line: /^(\d+) sockets \(api \d+, api-2 \d+\); 0 missing, 0 repeated deliveries$/, read: (m) => [m[1]!, null] },
  'NFR-28': {
    line: /^6\/6 ledger checks passed on (\d+) bids; concurrent-close tests 2\/2 passed \(OR-14\)$/,
    read: (m) => ['0', grouped.format(Number(m[1]))],
  },
};

/** The newest results file committed, by its date. */
export function latestResults(): ResultsFile | null {
  const newest = Object.keys(FILES).sort().at(-1);
  return newest === undefined ? null : FILES[newest]!;
}

/** The strip's figures, or null when any of them cannot be read exactly. */
export function extract(file: ResultsFile): Measured | null {
  const figures: Figure[] = [];
  for (const id of STRIP) {
    const row = file.requirements.find((r) => r.id === id);
    const m = row?.pass === true ? PATTERNS[id].line.exec(row.measured) : null;
    if (m === null || m === undefined) return null;
    const [value, detail] = PATTERNS[id].read(m);
    figures.push({ id, value, detail });
  }
  const { cpu, threads, ram_gib, docker, k6 } = file.machine;
  return {
    date: file.date,
    figures,
    machine: { cpu, threads, ramGib: ram_gib, dockerCpus: docker.cpus, dockerGib: docker.memory_gib, k6: k6.split(' ').slice(0, 2).join(' ') },
  };
}
