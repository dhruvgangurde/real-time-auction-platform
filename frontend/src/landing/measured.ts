// Brief 8.4, the numbers strip: measured results from the committed load-test results file
// (loadtest/results/<date>.json, written by `bash loadtest/run.sh`), read at build time. Only
// measured values, and only the requirements the brief lists. Since Phase 11 part 2 each row carries
// a figures block (slice 5 Q1 (b)), read when present; a file before it (2026-10-17) is read from
// each requirement's line of text by a strict pattern (Q1 (a)). A figures block or a line in any
// other form, or a requirement that did not pass, drops the whole strip rather than show a figure
// that might be wrong (measured.test.ts pins the committed figures).
const FILES = import.meta.glob<ResultsFile>('../../../loadtest/results/*.json', { eager: true, import: 'default' });

export type ResultsFile = {
  date: string;
  requirements: { id: string; target: string; measured: string; pass: boolean | null; figures?: Record<string, unknown> | null }[];
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

// the figures blocks loadtest-report writes (backend app/loadtest.py requirements())
type Read = [string, string | null] | null;
const decimals = (d: number) => new Intl.NumberFormat('en-US', { minimumFractionDigits: d, maximumFractionDigits: d, useGrouping: false });
const ms3 = decimals(3);
const ms1 = decimals(1);
const num = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v) && v >= 0;
const whole = (v: unknown): v is number => num(v) && Number.isSafeInteger(v);
const FIGURES: Record<(typeof STRIP)[number], (f: Record<string, unknown>) => Read> = {
  'NFR-1': (f) => (num(f.p95_ms) && whole(f.executions) && f.executions === f.decided ? [`${ms3.format(f.p95_ms)} ms`, grouped.format(f.executions)] : null),
  'NFR-2': (f) => (num(f.p95_ms) ? [`${ms1.format(f.p95_ms)} ms`, null] : null),
  'NFR-3': (f) => (num(f.p95_ms) ? [`${ms1.format(f.p95_ms)} ms`, null] : null),
  'NFR-4': (f) => (num(f.slowest_window_per_s) ? [ms1.format(f.slowest_window_per_s), null] : null),
  'NFR-5': (f) => (whole(f.sockets) && f.missing === 0 && f.duplicates === 0 ? [String(f.sockets), null] : null),
  'NFR-28': (f) =>
    whole(f.bids) && whole(f.checks) && f.checks > 0 && f.checks_passed === f.checks && f.concurrent_close_passed === true ? ['0', grouped.format(f.bids)] : null,
};

/** A committed results file by its date (its name), or null. */
export function resultsFile(date: string): ResultsFile | null {
  return FILES[`../../../loadtest/results/${date}.json`] ?? null;
}

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
    if (row?.pass !== true) return null;
    let read: Read;
    if (row.figures != null) {
      read = FIGURES[id](row.figures);
    } else {
      const m = PATTERNS[id].line.exec(row.measured);
      read = m === null ? null : PATTERNS[id].read(m);
    }
    if (read === null) return null;
    const [value, detail] = read;
    figures.push({ id, value, detail });
  }
  const { cpu, threads, ram_gib, docker, k6 } = file.machine;
  return {
    date: file.date,
    figures,
    machine: { cpu, threads, ramGib: ram_gib, dockerCpus: docker.cpus, dockerGib: docker.memory_gib, k6: k6.split(' ').slice(0, 2).join(' ') },
  };
}
