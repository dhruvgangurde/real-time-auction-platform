// Routes and their chunks (brief 11.5): the shell, Home and NotFound are in the entry; every
// other screen loads through a dynamic import, so the entry never holds room, socket or reducer
// code (src/app/chunks.test.ts). The token loaders run before anything renders (item 23).
import { lazy } from 'react';
import { createBrowserRouter } from 'react-router';

import { takeTokenFromHash } from '../auth/token';
import { PayDocument, RequireAuth, RequireRole } from './guards';
import { Home } from './Home';
import { NotFound } from './NotFound';
import { Shell } from './Shell';

// React.lazy inside RequireRole, not a route `lazy`: the router runs a matched route's `lazy`
// before the guard renders, so a non-admin would download the admin chunk (slice 4 plan)
const AdminArea = lazy(() => import('../admin/AdminArea'));
const account = (name: 'Profile') => () => import('../account/screens').then((m) => ({ Component: m[name] }));
const sell = (name: 'Dashboard' | 'NewListing' | 'Listing') => () => import('../sell/screens').then((m) => ({ Component: m[name] }));
const auth = (name: 'Register' | 'CheckYourEmail' | 'VerifyEmail' | 'SignIn' | 'ForgotPassword' | 'ResetPassword') => () =>
  import('../auth/screens').then((m) => ({ Component: m[name] }));

export const router = createBrowserRouter([
  {
    path: '/',
    element: <Shell />,
    children: [
      { index: true, element: <Home /> },
      { path: 'register', lazy: auth('Register') },
      { path: 'check-your-email', lazy: auth('CheckYourEmail') },
      { path: 'verify-email', lazy: auth('VerifyEmail'), loader: () => takeTokenFromHash() },
      { path: 'sign-in', lazy: auth('SignIn') },
      { path: 'forgot-password', lazy: auth('ForgotPassword') },
      { path: 'reset-password', lazy: auth('ResetPassword'), loader: () => takeTokenFromHash() },
      { path: 'auctions', lazy: () => import('../discover/DiscoverPage') },
      { path: 'auctions/:id', lazy: () => import('../room/RoomPage') },
      { path: 'auctions/:id/bids', lazy: () => import('../room/BidHistory') },
      { path: 'auctions/:id/pay', element: <PayDocument /> },
      { path: 'help', lazy: () => import('../help/HelpPage') },
      { path: 'help/:topic', lazy: () => import('../help/HelpPage') },
      {
        element: <RequireAuth />,
        children: [
          { path: 'account', lazy: account('Profile') },
          { path: 'watchlist', lazy: () => import('../watchlist/WatchlistPage') },
          { path: 'notifications', lazy: () => import('../notifications/NotificationsPage') },
        ],
      },
      { element: <RequireRole role="admin" />, children: [{ path: 'admin/*', element: <AdminArea /> }] },
      {
        element: <RequireRole role="seller" />,
        children: [
          { path: 'sell', lazy: sell('Dashboard') },
          { path: 'sell/new', lazy: sell('NewListing') },
          { path: 'sell/:id', lazy: sell('Listing') },
        ],
      },
      { path: '*', element: <NotFound /> },
    ],
  },
]);
