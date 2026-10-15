// Where sign-in returns to from `?next=` (the payment document links to /sign-in?next=<its path>).
// Only a path on this site: it must start with a single "/", with no backslash (browsers read one
// as a slash), no scheme and no control character (browsers drop tabs and newlines, so "/\t/x" is
// "//x"), in the raw value and in every decoding of it, so "%2F%2Fhost" style tricks fail too.
const SCHEME = /^[a-z][a-z0-9+.-]*:/i;
const MAX_DECODES = 5;

const control = (value: string) => [...value].some((c) => c.charCodeAt(0) < 0x20 || c.charCodeAt(0) === 0x7f);

function plainPath(value: string): boolean {
  return value.startsWith('/') && !value.startsWith('//') && !value.includes('\\') && !control(value) && !SCHEME.test(value);
}

export function safeNext(raw: string | null): string | null {
  if (raw === null) return null;
  let value = raw;
  for (let i = 0; i < MAX_DECODES; i += 1) {
    if (!plainPath(value)) return null;
    let decoded: string;
    try {
      decoded = decodeURIComponent(value);
    } catch {
      return null; // a malformed escape
    }
    if (decoded === value) {
      // every form passed; and the browser agrees it stays on this origin
      return new URL(raw, 'http://this.invalid').origin === 'http://this.invalid' ? raw : null;
    }
    value = decoded;
  }
  return null; // still decoding after MAX_DECODES rounds
}
