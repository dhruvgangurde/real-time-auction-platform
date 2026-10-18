// The colour theme the viewer chose: System (follow the device), Light or Dark. Kept in
// localStorage, which may be blocked or broken (then System); applied as data-theme on <html>,
// where styles/tokens.css picks it up. public/theme.js applies the stored choice in <head>, before
// first paint, in both documents (the SPA and the payment page); the CSP allows no inline script.
export type Theme = 'system' | 'light' | 'dark';

export const THEME_KEY = 'theme'; // also read by public/theme.js
const ORDER: readonly Theme[] = ['system', 'light', 'dark'];

type Store = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;
type Root = { dataset: Record<string, string | undefined>; removeAttribute: (name: string) => unknown };

export function readTheme(storage: Pick<Storage, 'getItem'> | null): Theme {
  try {
    const stored = storage?.getItem(THEME_KEY);
    return stored === 'light' || stored === 'dark' ? stored : 'system';
  } catch {
    return 'system';
  }
}

export function writeTheme(storage: Store | null, theme: Theme): void {
  try {
    if (theme === 'system') storage?.removeItem(THEME_KEY);
    else storage?.setItem(THEME_KEY, theme);
  } catch {
    // storage blocked: the choice lasts for this page only
  }
}

export function applyTheme(root: Root, theme: Theme): void {
  if (theme === 'system') root.removeAttribute('data-theme');
  else root.dataset.theme = theme;
}

export function nextTheme(theme: Theme): Theme {
  return ORDER[(ORDER.indexOf(theme) + 1) % ORDER.length]!;
}

/** localStorage, or null where reading the property itself throws (some blocked-storage modes). */
export function storage(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}
