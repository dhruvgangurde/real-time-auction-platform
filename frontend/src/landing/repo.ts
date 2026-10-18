// Links to the public repository (brief 8.4, 8.6, 8.7): from VITE_REPO_URL at build time
// (frontend/.env.development, .env.production); while it is unset, every such link is hidden.
const BASE = /^https:\/\/\S+$/;

export function repoLink(base: string | undefined, path: string): string | null {
  const root = base?.trim().replace(/\/+$/, '') ?? '';
  if (!BASE.test(root)) return null;
  return path === '' ? root : `${root}/blob/main/${path}`;
}

export const repo = (path: string): string | null => repoLink(import.meta.env.VITE_REPO_URL as string | undefined, path);
