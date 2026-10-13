// Money is integer paise end to end (DC-6, UI-7, brief 11.3). Formatting goes through BigInt and
// Intl.NumberFormat('en-IN') on whole rupees; parsing reads the user's text as a string. Nothing
// here ever calls parseFloat or toFixed (both are banned by lint).

const MAX_PAISE = Number.MAX_SAFE_INTEGER; // the API limit is 2^53 - 1
const rupeeFormat = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 });

export type Decimals = 'auto' | 'always';

export function isPaise(value: unknown): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;
}

function split(paise: number): { rupees: bigint; rem: bigint } {
  if (!isPaise(paise)) {
    throw new RangeError('money must be a non-negative safe integer of paise');
  }
  const whole = BigInt(paise);
  return { rupees: whole / 100n, rem: whole % 100n };
}

/** "₹4,25,000", "₹4,25,000.50" (paise only when non-zero), or always two decimals. */
export function formatINR(paise: number, decimals: Decimals = 'auto'): string {
  const { rupees, rem } = split(paise);
  const whole = `₹${rupeeFormat.format(rupees)}`;
  if (decimals === 'auto' && rem === 0n) {
    return whole;
  }
  return `${whole}.${rem.toString().padStart(2, '0')}`;
}

/** "4,25,000 rupees" / "4,25,000 rupees 50 paise": visually hidden text for screen readers. */
export function rupeesWords(paise: number): string {
  const { rupees, rem } = split(paise);
  const base = `${rupeeFormat.format(rupees)} rupees`;
  return rem === 0n ? base : `${base} ${rem.toString()} paise`;
}

export type ParseFailure = 'empty' | 'invalid' | 'negative' | 'precision' | 'unsafe';
export type Parsed = { ok: true; paise: number } | { ok: false; reason: ParseFailure };

// optional ₹, Indian or plain grouping, at most two decimals
const AMOUNT = /^(?:₹\s*)?(\d{1,3}(?:,\d{2,3})*|\d+)(?:\.(\d{1,2}))?$/;

/** Rupees typed by a user to paise: "4,25,000.5" -> 42500050; "4.255", "-1", "" fail. */
export function parseRupees(text: string): Parsed {
  const trimmed = text.trim();
  if (trimmed === '') {
    return { ok: false, reason: 'empty' };
  }
  if (trimmed.startsWith('-')) {
    return { ok: false, reason: 'negative' };
  }
  if (/^₹?\s*\d[\d,]*\.\d{3,}$/.test(trimmed)) {
    return { ok: false, reason: 'precision' };
  }
  const m = AMOUNT.exec(trimmed);
  if (m === null) {
    return { ok: false, reason: 'invalid' };
  }
  const rupees = BigInt(m[1]!.replace(/,/g, ''));
  const paise = BigInt((m[2] ?? '').padEnd(2, '0'));
  const total = rupees * 100n + paise;
  if (total > BigInt(MAX_PAISE)) {
    return { ok: false, reason: 'unsafe' };
  }
  return { ok: true, paise: Number(total) };
}
