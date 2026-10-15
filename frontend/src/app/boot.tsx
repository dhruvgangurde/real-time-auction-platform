// What every document of the app starts with (the SPA and the payment page): the styles, the
// in-memory session, the API client, one server clock, the string catalogue and the query cache.
// Neither document imports the other's code: the payment page carries no router (brief 13 item 25).
import '../styles/tokens.css';
import '../styles/fonts.css';
import '../styles/base.css';

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { StrictMode, type ReactNode } from 'react';
import { createRoot } from 'react-dom/client';

import { browserAuthDeps, createAuth } from '../api/auth';
import { createApi } from '../api/client';
import { ServerClock } from '../lib/clock';
import { StringsProvider } from '../strings/catalogue';
import { enIN } from '../strings/en-IN';
import { AppProvider, type AppServices } from './context';

export function boot(app: ReactNode): void {
  const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));
  const auth = createAuth({ fetch: (...args) => fetch(...args), now: () => Date.now(), sleep, ...browserAuthDeps() });
  const clock = new ServerClock(); // one server offset for the document (brief 13 item 12)
  const api = createApi({
    fetch: (...args) => fetch(...args),
    now: () => Date.now(),
    sleep,
    getToken: auth.getToken,
    refresh: auth.refresh,
    observe: (sample) => clock.observe(sample),
    ready: () => auth.ready,
  });
  const services: AppServices = { auth, api, clock };
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } } });

  // brief 13 item 22: on every load, a refresh (204 for an anonymous visitor) puts the access
  // token into memory; nothing is read from storage
  void auth.restoreOnLoad();

  const root = document.getElementById('root');
  if (root === null) {
    throw new Error('the document has no #root');
  }
  createRoot(root).render(
    <StrictMode>
      <StringsProvider catalogue={enIN}>
        <AppProvider value={services}>
          <QueryClientProvider client={queryClient}>{app}</QueryClientProvider>
        </AppProvider>
      </StringsProvider>
    </StrictMode>,
  );
}
