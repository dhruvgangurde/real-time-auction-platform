import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { RouterProvider } from 'react-router';

import './styles/tokens.css';
import './styles/fonts.css';
import './styles/base.css';

import { browserAuthDeps, createAuth } from './api/auth';
import { createApi } from './api/client';
import { AppProvider, type AppServices } from './app/context';
import { ServerClock } from './lib/clock';
import { router } from './app/routes';
import { StringsProvider } from './strings/catalogue';
import { enIN } from './strings/en-IN';

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));
const auth = createAuth({ fetch: (...args) => fetch(...args), now: () => Date.now(), sleep, ...browserAuthDeps() });
const clock = new ServerClock(); // one server offset for the app (brief 13 item 12)
const api = createApi({
  fetch: (...args) => fetch(...args),
  now: () => Date.now(),
  sleep,
  getToken: auth.getToken,
  refresh: auth.refresh,
  observe: (sample) => clock.observe(sample),
});
const services: AppServices = { auth, api, clock };
const queryClient = new QueryClient({ defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } } });

// brief 13 item 22: on every load, a refresh (204 for an anonymous visitor) puts the access
// token into memory; nothing is read from storage
void auth.restoreOnLoad();

const root = document.getElementById('root');
if (root === null) {
  throw new Error('index.html has no #root');
}
createRoot(root).render(
  <StrictMode>
    <StringsProvider catalogue={enIN}>
      <AppProvider value={services}>
        <QueryClientProvider client={queryClient}>
          <RouterProvider router={router} />
        </QueryClientProvider>
      </AppProvider>
    </StringsProvider>
  </StrictMode>,
);
