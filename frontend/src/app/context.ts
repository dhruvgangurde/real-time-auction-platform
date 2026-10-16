// The app's long-lived services (auth, the API client) and the hook that reads the in-memory
// token. Created once in main.tsx; components reach them through context, never through globals.
import { useQuery } from '@tanstack/react-query';
import { createContext, useContext, useMemo, useSyncExternalStore } from 'react';

import type { Auth } from '../api/auth';
import type { Api } from '../api/client';
import type { ServerClock } from '../lib/clock';

export type AppServices = { auth: Auth; api: Api; clock: ServerClock };

const AppContext = createContext<AppServices | null>(null);
export const AppProvider = AppContext.Provider;

export function useApp(): AppServices {
  const services = useContext(AppContext);
  if (services === null) throw new Error('useApp needs an AppProvider');
  return services;
}

/** The current access token (null when anonymous), re-rendering when it changes. */
export function useToken(): string | null {
  const { auth } = useApp();
  return useSyncExternalStore(auth.onChange, auth.getToken, () => null);
}

/** The user id in an access token's `sub`, or null. Only for keying per-user caches, so a refresh
 * (a new token, the same user) keeps them; the server checks the token itself. */
export function subject(token: string | null): string | null {
  const part = token?.split('.')[1];
  if (part === undefined) return null;
  try {
    const payload: unknown = JSON.parse(atob(part.replace(/-/g, '+').replace(/_/g, '/')));
    const sub = (payload as { sub?: unknown } | null)?.sub;
    return typeof sub === 'string' ? sub : null;
  } catch {
    return null;
  }
}

/** The profile query's key: the user, not the token, so a refresh never empties it (a guarded
 * page would unmount and lose a half-filled form). */
export const meKey = (token: string | null) => ['me', subject(token)] as const;

/** The signed-in user's profile, roles from the database; one cache for the shell, guards and room. */
export function useMe() {
  const { api } = useApp();
  const token = useToken();
  return useQuery({ queryKey: meKey(token), queryFn: () => api.me(), enabled: token !== null, staleTime: 60_000 });
}

/** The signed-in user's id (null when anonymous), stable across token refreshes. */
export function useViewerId(): string | null {
  const token = useToken();
  return useMemo(() => subject(token), [token]);
}

/** False until the load-time restore settled: a guard must not redirect before then. */
export function useAuthReady(): boolean {
  const { auth } = useApp();
  return useSyncExternalStore(auth.onChange, auth.isRestored, () => false);
}

/** The load-time restore could not reach the server (network, 5xx): not the same as signed out. */
export function useAuthUnreachable(): boolean {
  const { auth } = useApp();
  return useSyncExternalStore(auth.onChange, auth.isUnreachable, () => false);
}
