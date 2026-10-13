// The app's long-lived services (auth, the API client) and the hook that reads the in-memory
// token. Created once in main.tsx; components reach them through context, never through globals.
import { createContext, useContext, useSyncExternalStore } from 'react';

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

/** False until the load-time restore settled: a guard must not redirect before then. */
export function useAuthReady(): boolean {
  const { auth } = useApp();
  return useSyncExternalStore(auth.onChange, auth.isRestored, () => false);
}
