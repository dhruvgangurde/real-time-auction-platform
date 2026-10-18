// brief 11.5: the entry chunk holds no room, socket or reducer code, and no screen of slices 3 and 4.
// Routes load those with dynamic imports (route-level lazy); nothing in the entry may import
// them statically.
import { expect, test } from 'vitest';

import main from '../main.tsx?raw';
import payMain from '../pay/main.tsx?raw';
import payPage from '../pay/PayPage.tsx?raw';
import roomPage from '../room/RoomPage.tsx?raw';
import { enIN } from '../strings/en-IN';
import boot from './boot.tsx?raw';
import home from './Home.tsx?raw';
import routes from './routes.tsx?raw';
import shell from './Shell.tsx?raw';

const STATIC_IMPORT = /^import\s[^;]*from\s+['"]([^'"]+)['"]/gm;
const LIVE = /(^|\/)(live|room|discover|watchlist|notifications|sell|admin|account|landing)\//;

// brief 8.1: the landing's h1 is in the entry (it is the LCP element); its body, its strings and
// its sections load as one lazy chunk, so the entry grows by the h1 alone
test('Home loads the landing body lazily and imports nothing of it statically', () => {
  const targets = [...home.matchAll(STATIC_IMPORT)].map((m) => m[1]!);
  expect(targets.filter((t) => /(^|\/)landing\/|landing-en-IN/.test(t))).toEqual([]);
  expect(home).toMatch(/import\('\.\.\/landing\/LandingBody'\)/);
});

for (const [name, source] of Object.entries({ main, routes, shell })) {
  test(`${name} imports no live or room module statically`, () => {
    const targets = [...source.matchAll(STATIC_IMPORT)].map((m) => m[1]!);
    expect(targets.filter((t) => LIVE.test(t))).toEqual([]);
  });
}

// brief 13 item 25: the payment document is its own build; it carries no router and no SPA screen
const NOT_IN_PAY = /^react-router$|(^|\/)(live|room|discover|watchlist|notifications|auth|sell|admin|account)\//;
for (const [name, source] of Object.entries({ boot, payMain, payPage })) {
  test(`${name} imports no router and no SPA screen`, () => {
    const targets = [...source.matchAll(STATIC_IMPORT)].map((m) => m[1]!);
    expect(targets.filter((t) => NOT_IN_PAY.test(t))).toEqual([]);
  });
}

// a non-admin never downloads admin code (slice 4 plan, Q4): the room loads its admin panel lazily,
// only for an administrator, and the entry's catalogue carries no admin strings
// the help topics' long strings are their own module (strings/help-en-IN.ts), for the help, room
// and sell chunks; the shared catalogue, in the entry, has none of them
test('the entry carries no help topic strings', () => {
  for (const source of [main, routes, shell, home]) {
    const targets = [...source.matchAll(STATIC_IMPORT)].map((m) => m[1]!);
    expect(targets.filter((t) => /help-en-IN|(^|\/)help\//.test(t))).toEqual([]);
  }
  expect('help' in enIN).toBe(false);
});

test('the room imports no admin module statically, and the shared catalogue has no admin strings', () => {
  const targets = [...roomPage.matchAll(STATIC_IMPORT)].map((m) => m[1]!);
  expect(targets.filter((t) => /(^|\/)admin\/|AdminPanel/.test(t))).toEqual([]);
  expect(Object.keys(enIN.roomUi)).not.toContain('admin');
});
