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
import routes from './routes.tsx?raw';
import shell from './Shell.tsx?raw';

const STATIC_IMPORT = /^import\s[^;]*from\s+['"]([^'"]+)['"]/gm;
const LIVE = /(^|\/)(live|room|discover|watchlist|notifications|sell|admin|account)\//;

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
test('the room imports no admin module statically, and the shared catalogue has no admin strings', () => {
  const targets = [...roomPage.matchAll(STATIC_IMPORT)].map((m) => m[1]!);
  expect(targets.filter((t) => /(^|\/)admin\/|AdminPanel/.test(t))).toEqual([]);
  expect(Object.keys(enIN.roomUi)).not.toContain('admin');
});
