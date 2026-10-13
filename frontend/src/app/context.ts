// The app's long-lived services (auth, the API client) and the hook that reads the in-memory
// token. Created once in main.tsx; components reach them through context, never through globals.
import { createContext, useContext, useSyncExternalStore } from 'react';

import type { Auth } from '../api/auth';
import type { Api } from '../api/client';

export type AppServices = { auth: Auth; api: Api };

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
