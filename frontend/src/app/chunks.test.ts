// brief 11.5: the entry chunk holds no room, socket or reducer code, and no screen of slice 3.
// Routes load those with dynamic imports (route-level lazy); nothing in the entry may import
// them statically.
import { expect, test } from 'vitest';

import main from '../main.tsx?raw';
import routes from './routes.tsx?raw';
import shell from './Shell.tsx?raw';

const STATIC_IMPORT = /^import\s[^;]*from\s+['"]([^'"]+)['"]/gm;
const LIVE = /(^|\/)(live|room|discover|watchlist|notifications)\//;

for (const [name, source] of Object.entries({ main, routes, shell })) {
  test(`${name} imports no live or room module statically`, () => {
    const targets = [...source.matchAll(STATIC_IMPORT)].map((m) => m[1]!);
    expect(targets.filter((t) => LIVE.test(t))).toEqual([]);
  });
}
